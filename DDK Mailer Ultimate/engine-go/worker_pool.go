package main

import (
	"encoding/base64"
	"errors"
	"net/smtp"
)

// LoginAuth implementa AUTH LOGIN (fallback quando PLAIN falha)
type loginAuth struct {
	username, password string
}

func LoginAuth(username, password string) smtp.Auth {
	return &loginAuth{username, password}
}

func (a *loginAuth) Start(server *smtp.ServerInfo) (string, []byte, error) {
	return "LOGIN", []byte{}, nil
}

func (a *loginAuth) Next(fromServer []byte, more bool) ([]byte, error) {
	if more {
		switch string(fromServer) {
		case "Username:":
			return []byte(a.username), nil
		case "Password:":
			return []byte(a.password), nil
		default:
			// Alguns servidores enviam prompts em base64
			decoded, err := base64.StdEncoding.DecodeString(string(fromServer))
			if err == nil {
				switch string(decoded) {
				case "Username:":
					return []byte(a.username), nil
				case "Password:":
					return []byte(a.password), nil
				}
			}
			return nil, errors.New("prompt desconhecido do servidor: " + string(fromServer))
		}
	}
	return nil, nil
}