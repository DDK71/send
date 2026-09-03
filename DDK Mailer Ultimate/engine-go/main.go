package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// Estruturas de comunicação com Python (via stdin/stdout JSON)
type SendRequest struct {
	Command  string       `json:"command"`
	Server   SMTPServer   `json:"server"`
	Proxy    *ProxyConfig `json:"proxy,omitempty"`
	Task     SendTask     `json:"task"`
	PoolSize int          `json:"pool_size,omitempty"`
}

type SendResponse struct {
	Success  bool    `json:"success"`
	Error    string  `json:"error,omitempty"`
	Latency  float64 `json:"latency_ms"`
	Recipient string `json:"recipient"`
	SMTPHost string  `json:"smtp_host"`
}

type BatchRequest struct {
	Command  string       `json:"command"`
	Server   SMTPServer   `json:"server"`
	Proxy    *ProxyConfig `json:"proxy,omitempty"`
	Tasks    []SendTask   `json:"tasks"`
	MaxConns int          `json:"max_conns"`
}

var globalPool *ConnectionPool
var poolMutex sync.Mutex

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 1024*1024), 10*1024*1024) // 10MB buffer

	encoder := json.NewEncoder(os.Stdout)

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}

		// Detecta tipo de comando
		var probe map[string]interface{}
		if err := json.Unmarshal([]byte(line), &probe); err != nil {
			encoder.Encode(SendResponse{Success: false, Error: "JSON inválido: " + err.Error()})
			continue
		}

		command, _ := probe["command"].(string)

		switch command {
		case "send":
			handleSingleSend(line, encoder)
		case "batch":
			handleBatchSend(line, encoder)
		case "ping":
			encoder.Encode(map[string]interface{}{"success": true, "message": "pong", "version": "1.0"})
		case "shutdown":
			if globalPool != nil {
				globalPool.CloseAll()
			}
			return
		default:
			encoder.Encode(SendResponse{Success: false, Error: "Comando desconhecido: " + command})
		}
	}
}

func handleSingleSend(line string, encoder *json.Encoder) {
	var req SendRequest
	if err := json.Unmarshal([]byte(line), &req); err != nil {
		encoder.Encode(SendResponse{Success: false, Error: err.Error()})
		return
	}

	start := time.Now()
	err := SendEmail(req.Server, req.Proxy, req.Task)
	latency := float64(time.Since(start).Milliseconds())

	resp := SendResponse{
		Success:   err == nil,
		Latency:   latency,
		Recipient: req.Task.To,
		SMTPHost:  req.Server.Host,
	}
	if err != nil {
		resp.Error = err.Error()
	}
	encoder.Encode(resp)
}

func handleBatchSend(line string, encoder *json.Encoder) {
	var req BatchRequest
	if err := json.Unmarshal([]byte(line), &req); err != nil {
		encoder.Encode(SendResponse{Success: false, Error: err.Error()})
		return
	}

	maxConns := req.MaxConns
	if maxConns < 1 {
		maxConns = 5
	}

	results := SendBatch(req.Server, req.Proxy, req.Tasks, maxConns)

	for _, r := range results {
		encoder.Encode(r)
	}

	// Marcador de fim de batch
	encoder.Encode(map[string]interface{}{"batch_complete": true, "total": len(results)})
}

func init() {
	// Log para stderr (não interfere com JSON no stdout)
	fmt.Fprintln(os.Stderr, "[GO-ENGINE] Iniciado com sucesso")
}