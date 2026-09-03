package main

import (
	"fmt"
	"net/smtp"
	"sync"
	"time"
)

type PooledConnection struct {
	Client    *smtp.Client
	Server    SMTPServer
	CreatedAt time.Time
	LastUsed  time.Time
	InUse     bool
	SendCount int
}

type ConnectionPool struct {
	connections map[string][]*PooledConnection
	mu          sync.Mutex
	maxPerHost  int
	maxLifetime time.Duration
	maxSends    int
}

func NewConnectionPool(maxPerHost, maxSends int, maxLifetime time.Duration) *ConnectionPool {
	return &ConnectionPool{
		connections: make(map[string][]*PooledConnection),
		maxPerHost:  maxPerHost,
		maxLifetime: maxLifetime,
		maxSends:    maxSends,
	}
}

func (p *ConnectionPool) key(server SMTPServer) string {
	return fmt.Sprintf("%s:%d:%s", server.Host, server.Port, server.Username)
}

func (p *ConnectionPool) CloseAll() {
	p.mu.Lock()
	defer p.mu.Unlock()

	for _, conns := range p.connections {
		for _, c := range conns {
			if c.Client != nil {
				c.Client.Quit()
			}
		}
	}
	p.connections = make(map[string][]*PooledConnection)
}