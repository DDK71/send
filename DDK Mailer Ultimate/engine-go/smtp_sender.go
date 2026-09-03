package main

import (
	"crypto/tls"
	"fmt"
	"net"
	"net/smtp"
	"strings"
	"time"

	"golang.org/x/net/proxy"
)

type SMTPServer struct {
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Username string `json:"username"`
	Password string `json:"password"`
	UseSSL   bool   `json:"use_ssl"`
}

type ProxyConfig struct {
	Type     string `json:"type"` // socks5, socks4, http
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Username string `json:"username,omitempty"`
	Password string `json:"password,omitempty"`
}

type SendTask struct {
	From       string            `json:"from"`
	FromName   string            `json:"from_name,omitempty"`
	To         string            `json:"to"`
	RawMessage string            `json:"raw_message"` // MIME já montado no Python
	Headers    map[string]string `json:"headers,omitempty"`
}

// dialConnection cria conexão TCP com ou sem proxy
func dialConnection(server SMTPServer, proxyCfg *ProxyConfig, timeout time.Duration) (net.Conn, error) {
	addr := fmt.Sprintf("%s:%d", server.Host, server.Port)

	if proxyCfg == nil {
		return net.DialTimeout("tcp", addr, timeout)
	}

	proxyAddr := fmt.Sprintf("%s:%d", proxyCfg.Host, proxyCfg.Port)

	switch strings.ToLower(proxyCfg.Type) {
	case "socks5", "socks4":
		var auth *proxy.Auth
		if proxyCfg.Username != "" {
			auth = &proxy.Auth{
				User:     proxyCfg.Username,
				Password: proxyCfg.Password,
			}
		}
		dialer, err := proxy.SOCKS5("tcp", proxyAddr, auth, &net.Dialer{Timeout: timeout})
		if err != nil {
			return nil, fmt.Errorf("erro SOCKS5: %w", err)
		}
		return dialer.Dial("tcp", addr)
	default:
		return nil, fmt.Errorf("tipo de proxy não suportado: %s", proxyCfg.Type)
	}
}

// SendEmail envia UM único email, criando e destruindo conexão
func SendEmail(server SMTPServer, proxyCfg *ProxyConfig, task SendTask) error {
	timeout := 15 * time.Second

	conn, err := dialConnection(server, proxyCfg, timeout)
	if err != nil {
		return fmt.Errorf("dial falhou: %w", err)
	}
	defer conn.Close()

	conn.SetDeadline(time.Now().Add(30 * time.Second))

	var client *smtp.Client

	// SSL direto (porta 465)
	if server.UseSSL || server.Port == 465 {
		tlsConfig := &tls.Config{
			ServerName:         server.Host,
			InsecureSkipVerify: false,
			MinVersion:         tls.VersionTLS12,
		}
		tlsConn := tls.Client(conn, tlsConfig)
		if err := tlsConn.Handshake(); err != nil {
			return fmt.Errorf("TLS handshake: %w", err)
		}
		client, err = smtp.NewClient(tlsConn, server.Host)
	} else {
		client, err = smtp.NewClient(conn, server.Host)
	}

	if err != nil {
		return fmt.Errorf("cliente SMTP: %w", err)
	}
	defer client.Quit()

	// EHLO
	if err := client.Hello("localhost"); err != nil {
		return fmt.Errorf("EHLO: %w", err)
	}

	// STARTTLS (portas 587, 25, 2525)
	if !server.UseSSL && server.Port != 465 {
		if ok, _ := client.Extension("STARTTLS"); ok {
			tlsConfig := &tls.Config{
				ServerName: server.Host,
				MinVersion: tls.VersionTLS12,
			}
			if err := client.StartTLS(tlsConfig); err != nil {
				return fmt.Errorf("STARTTLS: %w", err)
			}
		}
	}

	// AUTH
	if server.Username != "" {
		auth := smtp.PlainAuth("", server.Username, server.Password, server.Host)
		if err := client.Auth(auth); err != nil {
			// Fallback LOGIN
			auth = LoginAuth(server.Username, server.Password)
			if err2 := client.Auth(auth); err2 != nil {
				return fmt.Errorf("AUTH falhou: %w", err)
			}
		}
	}

	// MAIL FROM
	if err := client.Mail(server.Username); err != nil {
		return fmt.Errorf("MAIL FROM: %w", err)
	}

	// RCPT TO
	if err := client.Rcpt(task.To); err != nil {
		return fmt.Errorf("RCPT TO: %w", err)
	}

	// DATA
	w, err := client.Data()
	if err != nil {
		return fmt.Errorf("DATA: %w", err)
	}

	_, err = w.Write([]byte(task.RawMessage))
	if err != nil {
		return fmt.Errorf("write body: %w", err)
	}

	if err := w.Close(); err != nil {
		return fmt.Errorf("close body: %w", err)
	}

	return nil
}

// SendBatch envia múltiplos emails reutilizando UMA conexão (connection pooling)
func SendBatch(server SMTPServer, proxyCfg *ProxyConfig, tasks []SendTask, maxConcurrent int) []SendResponse {
	results := make([]SendResponse, 0, len(tasks))
	timeout := 20 * time.Second

	conn, err := dialConnection(server, proxyCfg, timeout)
	if err != nil {
		for _, t := range tasks {
			results = append(results, SendResponse{
				Success:   false,
				Error:     "dial: " + err.Error(),
				Recipient: t.To,
				SMTPHost:  server.Host,
			})
		}
		return results
	}
	defer conn.Close()

	var client *smtp.Client

	if server.UseSSL || server.Port == 465 {
		tlsConfig := &tls.Config{ServerName: server.Host, MinVersion: tls.VersionTLS12}
		tlsConn := tls.Client(conn, tlsConfig)
		if err := tlsConn.Handshake(); err != nil {
			for _, t := range tasks {
				results = append(results, SendResponse{Success: false, Error: "TLS: " + err.Error(), Recipient: t.To})
			}
			return results
		}
		client, err = smtp.NewClient(tlsConn, server.Host)
	} else {
		client, err = smtp.NewClient(conn, server.Host)
	}

	if err != nil {
		for _, t := range tasks {
			results = append(results, SendResponse{Success: false, Error: "client: " + err.Error(), Recipient: t.To})
		}
		return results
	}
	defer client.Quit()

	if err := client.Hello("localhost"); err != nil {
		for _, t := range tasks {
			results = append(results, SendResponse{Success: false, Error: "EHLO: " + err.Error(), Recipient: t.To})
		}
		return results
	}

	if !server.UseSSL && server.Port != 465 {
		if ok, _ := client.Extension("STARTTLS"); ok {
			tlsConfig := &tls.Config{ServerName: server.Host, MinVersion: tls.VersionTLS12}
			if err := client.StartTLS(tlsConfig); err != nil {
				for _, t := range tasks {
					results = append(results, SendResponse{Success: false, Error: "STARTTLS: " + err.Error(), Recipient: t.To})
				}
				return results
			}
		}
	}

	if server.Username != "" {
		auth := smtp.PlainAuth("", server.Username, server.Password, server.Host)
		if err := client.Auth(auth); err != nil {
			auth = LoginAuth(server.Username, server.Password)
			if err2 := client.Auth(auth); err2 != nil {
				for _, t := range tasks {
					results = append(results, SendResponse{Success: false, Error: "AUTH: " + err.Error(), Recipient: t.To})
				}
				return results
			}
		}
	}

	// Envia todos os emails reutilizando a conexão
	for _, task := range tasks {
		start := time.Now()

		if err := client.Reset(); err != nil {
			results = append(results, SendResponse{Success: false, Error: "RSET: " + err.Error(), Recipient: task.To})
			// Conexão pode estar quebrada, aborta batch
			break
		}

		if err := client.Mail(server.Username); err != nil {
			results = append(results, SendResponse{Success: false, Error: "MAIL: " + err.Error(), Recipient: task.To})
			continue
		}

		if err := client.Rcpt(task.To); err != nil {
			results = append(results, SendResponse{Success: false, Error: "RCPT: " + err.Error(), Recipient: task.To})
			continue
		}

		w, err := client.Data()
		if err != nil {
			results = append(results, SendResponse{Success: false, Error: "DATA: " + err.Error(), Recipient: task.To})
			continue
		}

		_, err = w.Write([]byte(task.RawMessage))
		if err != nil {
			results = append(results, SendResponse{Success: false, Error: "write: " + err.Error(), Recipient: task.To})
			w.Close()
			continue
		}

		if err := w.Close(); err != nil {
			results = append(results, SendResponse{Success: false, Error: "close: " + err.Error(), Recipient: task.To})
			continue
		}

		latency := float64(time.Since(start).Milliseconds())
		results = append(results, SendResponse{
			Success:   true,
			Latency:   latency,
			Recipient: task.To,
			SMTPHost:  server.Host,
		})
	}

	return results
}