package mcp_test

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"mcp-server/internal/mcp"
	"mcp-server/internal/tools"
)

func newServer() *mcp.Server {
	return mcp.NewServer(tools.NewRegistry())
}

// roundtrip sends one JSON-RPC line and returns the decoded response.
func roundtrip(t *testing.T, srv *mcp.Server, req mcp.Request) mcp.Response {
	t.Helper()
	b, _ := json.Marshal(req)
	var out bytes.Buffer
	if err := srv.Serve(strings.NewReader(string(b)+"\n"), &out); err != nil {
		t.Fatalf("serve: %v", err)
	}
	var resp mcp.Response
	if err := json.Unmarshal(out.Bytes(), &resp); err != nil {
		t.Fatalf("unmarshal (raw=%s): %v", out.String(), err)
	}
	return resp
}

// multiRoundtrip sends multiple JSON-RPC lines and returns all decoded responses.
func multiRoundtrip(t *testing.T, srv *mcp.Server, reqs []mcp.Request) []mcp.Response {
	t.Helper()
	var sb strings.Builder
	for _, r := range reqs {
		b, _ := json.Marshal(r)
		sb.Write(b)
		sb.WriteByte('\n')
	}
	var out bytes.Buffer
	if err := srv.Serve(strings.NewReader(sb.String()), &out); err != nil {
		t.Fatalf("serve: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	resps := make([]mcp.Response, len(lines))
	for i, line := range lines {
		json.Unmarshal([]byte(line), &resps[i])
	}
	return resps
}

func toolCall(t *testing.T, srv *mcp.Server, name string, args map[string]any) mcp.Response {
	t.Helper()
	return roundtrip(t, srv, mcp.Request{
		JSONRPC: "2.0", ID: 1, Method: "tools/call",
		Params: map[string]any{"name": name, "arguments": args},
	})
}

func assertNoRPCError(t *testing.T, resp mcp.Response) {
	t.Helper()
	if resp.Error != nil {
		t.Fatalf("unexpected RPC error: code=%d msg=%s", resp.Error.Code, resp.Error.Message)
	}
}

func assertNotToolError(t *testing.T, resp mcp.Response) {
	t.Helper()
	result, _ := resp.Result.(map[string]any)
	if result["isError"] == true {
		content, _ := result["content"].([]any)
		if len(content) > 0 {
			block, _ := content[0].(map[string]any)
			t.Fatalf("tool returned isError=true: %v", block["text"])
		}
		t.Fatal("tool returned isError=true")
	}
}

// ---------------------------------------------------------------------------
// Protocol
// ---------------------------------------------------------------------------

func TestInitialize(t *testing.T) {
	resp := roundtrip(t, newServer(), mcp.Request{
		JSONRPC: "2.0", ID: 1, Method: "initialize",
		Params: map[string]any{"protocolVersion": "2024-11-05"},
	})
	assertNoRPCError(t, resp)
	result, _ := resp.Result.(map[string]any)
	if result["protocolVersion"] != "2024-11-05" {
		t.Errorf("protocolVersion = %v", result["protocolVersion"])
	}
}

func TestToolsListContainsAllTools(t *testing.T) {
	resp := roundtrip(t, newServer(), mcp.Request{
		JSONRPC: "2.0", ID: 2, Method: "tools/list",
	})
	assertNoRPCError(t, resp)

	result, _ := resp.Result.(map[string]any)
	toolList, _ := result["tools"].([]any)
	nameSet := map[string]bool{}
	for _, item := range toolList {
		m, _ := item.(map[string]any)
		if n, _ := m["name"].(string); n != "" {
			nameSet[n] = true
		}
	}
	for _, want := range []string{
		"memory_set", "memory_get", "memory_list",
		"web_search", "web_fetch",
		"file_read", "file_write", "file_list",
		"http_request",
	} {
		if !nameSet[want] {
			t.Errorf("missing tool %q", want)
		}
	}
}

func TestUnknownMethod(t *testing.T) {
	resp := roundtrip(t, newServer(), mcp.Request{
		JSONRPC: "2.0", ID: 9, Method: "no/such/method",
	})
	if resp.Error == nil || resp.Error.Code != mcp.CodeMethodNotFound {
		t.Errorf("expected CodeMethodNotFound, got: %+v", resp.Error)
	}
}

func TestUnknownTool(t *testing.T) {
	resp := toolCall(t, newServer(), "does_not_exist", map[string]any{})
	assertNoRPCError(t, resp)
	result, _ := resp.Result.(map[string]any)
	if result["isError"] != true {
		t.Error("expected isError=true for unknown tool")
	}
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

func TestMemorySetGetRoundTrip(t *testing.T) {
	resps := multiRoundtrip(t, newServer(), []mcp.Request{
		{JSONRPC: "2.0", ID: 1, Method: "tools/call",
			Params: map[string]any{"name": "memory_set", "arguments": map[string]any{
				"key": "goal", "value": "analyse Q3 data",
			}}},
		{JSONRPC: "2.0", ID: 2, Method: "tools/call",
			Params: map[string]any{"name": "memory_get", "arguments": map[string]any{
				"key": "goal",
			}}},
	})
	if len(resps) != 2 {
		t.Fatalf("expected 2 responses, got %d", len(resps))
	}
	assertNoRPCError(t, resps[0])
	assertNotToolError(t, resps[0])
	assertNoRPCError(t, resps[1])
	assertNotToolError(t, resps[1])
}

func TestMemoryGetMissingKey(t *testing.T) {
	resp := toolCall(t, newServer(), "memory_get", map[string]any{"key": "nonexistent"})
	assertNoRPCError(t, resp)
	result, _ := resp.Result.(map[string]any)
	if result["isError"] != true {
		t.Error("expected isError=true for missing key")
	}
}

func TestMemoryList(t *testing.T) {
	resp := toolCall(t, newServer(), "memory_list", map[string]any{})
	assertNoRPCError(t, resp)
	assertNotToolError(t, resp)
}

func TestMemoryMissingRequiredArgs(t *testing.T) {
	cases := []struct {
		tool string
		args map[string]any
	}{
		{"memory_set", map[string]any{"key": "k"}},   // missing value
		{"memory_set", map[string]any{"value": "v"}}, // missing key
		{"memory_get", map[string]any{}},             // missing key
	}
	for _, tc := range cases {
		t.Run(tc.tool, func(t *testing.T) {
			resp := toolCall(t, newServer(), tc.tool, tc.args)
			assertNoRPCError(t, resp)
			result, _ := resp.Result.(map[string]any)
			if result["isError"] != true {
				t.Error("expected isError=true")
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

func TestFileWriteReadRoundTrip(t *testing.T) {
	t.Setenv("FILE_WORK_DIR", t.TempDir())
	resps := multiRoundtrip(t, newServer(), []mcp.Request{
		{JSONRPC: "2.0", ID: 1, Method: "tools/call",
			Params: map[string]any{"name": "file_write", "arguments": map[string]any{
				"path": "test.txt", "content": "hello world",
			}}},
		{JSONRPC: "2.0", ID: 2, Method: "tools/call",
			Params: map[string]any{"name": "file_read", "arguments": map[string]any{
				"path": "test.txt",
			}}},
	})
	if len(resps) != 2 {
		t.Fatalf("expected 2 responses, got %d", len(resps))
	}
	assertNoRPCError(t, resps[0])
	assertNotToolError(t, resps[0])
	assertNoRPCError(t, resps[1])
	assertNotToolError(t, resps[1])
}

func TestFileList(t *testing.T) {
	t.Setenv("FILE_WORK_DIR", t.TempDir())
	resp := toolCall(t, newServer(), "file_list", map[string]any{})
	assertNoRPCError(t, resp)
	assertNotToolError(t, resp)
}

func TestFilePathTraversalBlocked(t *testing.T) {
	t.Setenv("FILE_WORK_DIR", t.TempDir())
	resp := toolCall(t, newServer(), "file_read", map[string]any{
		"path": "../../etc/passwd",
	})
	assertNoRPCError(t, resp)
	result, _ := resp.Result.(map[string]any)
	if result["isError"] != true {
		t.Error("expected isError=true for path traversal attempt")
	}
}

func TestFileReadMissing(t *testing.T) {
	t.Setenv("FILE_WORK_DIR", t.TempDir())
	resp := toolCall(t, newServer(), "file_read", map[string]any{
		"path": "does_not_exist.txt",
	})
	assertNoRPCError(t, resp)
	result, _ := resp.Result.(map[string]any)
	if result["isError"] != true {
		t.Error("expected isError=true for missing file")
	}
}
