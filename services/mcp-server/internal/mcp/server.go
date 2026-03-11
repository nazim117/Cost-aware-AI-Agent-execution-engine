package mcp

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
)

// Server runs the MCP stdio transport.
type Server struct {
	registry ToolHandler
}

// NewServer creates a Server. Pass tools.NewRegistry() as the handler.
func NewServer(h ToolHandler) *Server {
	return &Server{registry: h}
}

// Serve blocks, reading newline-delimited JSON-RPC messages from r and
// writing responses to w. Returns when r is closed.
func (s *Server) Serve(r io.Reader, w io.Writer) error {
	enc := json.NewEncoder(w)
	scanner := bufio.NewScanner(r)

	log.Println("ready — listening on stdin")

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}

		var req Request
		if err := json.Unmarshal(line, &req); err != nil {
			_ = enc.Encode(errResp(nil, CodeParseError, "parse error: "+err.Error()))
			continue
		}

		resp := s.dispatch(req)
		if resp == nil {
			continue // notifications have no response
		}
		if err := enc.Encode(resp); err != nil {
			return fmt.Errorf("encode: %w", err)
		}
	}

	return scanner.Err()
}

func (s *Server) dispatch(req Request) *Response {
	log.Printf("← %s (id=%v)", req.Method, req.ID)

	switch req.Method {
	case "initialize":
		return s.handleInitialize(req)

	case "initialized":
		return nil // notification — no response

	case "tools/list":
		return ok(req.ID, ToolsListResult{Tools: s.registry.Definitions()})

	case "tools/call":
		return s.handleToolsCall(req)

	case "ping":
		return ok(req.ID, struct{}{})

	default:
		return errResp(req.ID, CodeMethodNotFound, "method not found: "+req.Method)
	}
}

func (s *Server) handleInitialize(req Request) *Response {
	return ok(req.ID, InitializeResult{
		ProtocolVersion: "2024-11-05",
		ServerInfo:      ServerInfo{Name: "cost-aware-agent-engine", Version: "0.1.0"},
		Capabilities:    Capability{Tools: &ToolsCapability{ListChanged: false}},
	})
}

func (s *Server) handleToolsCall(req Request) *Response {
	raw, err := json.Marshal(req.Params)
	if err != nil {
		return errResp(req.ID, CodeInvalidParams, "cannot encode params")
	}

	var p ToolCallParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return errResp(req.ID, CodeInvalidParams, "invalid params: "+err.Error())
	}

	result, callErr := s.registry.Call(p.Name, p.Arguments)
	if callErr != nil {
		return ok(req.ID, ToolCallResult{
			Content: []ContentBlock{{Type: "text", Text: callErr.Error()}},
			IsError: true,
		})
	}

	return ok(req.ID, result)
}

// helpers

func ok(id any, result any) *Response {
	return &Response{JSONRPC: "2.0", ID: id, Result: result}
}

func errResp(id any, code int, msg string) *Response {
	return &Response{JSONRPC: "2.0", ID: id, Error: &RPCError{Code: code, Message: msg}}
}
