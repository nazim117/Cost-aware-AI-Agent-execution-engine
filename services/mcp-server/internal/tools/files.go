package tools

import (
	"fmt"
	"os"
	"path/filepath"

	"mcp-server/internal/mcp"
)

// workDir is the root the agent is allowed to read/write within.
// Override via the FILE_WORK_DIR environment variable.
func workDir() string {
	if d := os.Getenv("FILE_WORK_DIR"); d != "" {
		return d
	}
	return "./agent-workspace"
}

// safePath resolves p relative to workDir and ensures it doesn't escape via
// path traversal. Returns an error result if the path is unsafe.
func safePath(p string) (string, *mcp.ToolCallResult, error) {
	root, err := filepath.Abs(workDir())
	if err != nil {
		r, e := textErr(fmt.Sprintf("cannot resolve work dir: %v", err))
		return "", &r, e
	}

	abs, err := filepath.Abs(filepath.Join(root, p))
	if err != nil {
		r, e := textErr(fmt.Sprintf("cannot resolve path: %v", err))
		return "", &r, e
	}

	// Prevent path traversal outside the work directory.
	rel, err := filepath.Rel(root, abs)
	if err != nil || len(rel) >= 2 && rel[:2] == ".." {
		r, e := textErr(fmt.Sprintf("path %q escapes the workspace", p))
		return "", &r, e
	}

	return abs, nil, nil
}

func fileRead(args map[string]any) (mcp.ToolCallResult, error) {
	path, errResult, err := requireString(args, "path")
	if errResult != nil {
		return *errResult, err
	}

	abs, errResult, err := safePath(path)
	if errResult != nil {
		return *errResult, err
	}

	data, err := os.ReadFile(abs)
	if err != nil {
		return textErr(fmt.Sprintf("read file failed: %v", err))
	}

	return textResult(map[string]any{
		"path":    path,
		"content": string(data),
		"bytes":   len(data),
	})
}

func fileWrite(args map[string]any) (mcp.ToolCallResult, error) {
	path, errResult, err := requireString(args, "path")
	if errResult != nil {
		return *errResult, err
	}
	content, errResult, err := requireString(args, "content")
	if errResult != nil {
		return *errResult, err
	}

	abs, errResult, err := safePath(path)
	if errResult != nil {
		return *errResult, err
	}

	// Ensure parent directories exist.
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return textErr(fmt.Sprintf("create directories failed: %v", err))
	}

	if err := os.WriteFile(abs, []byte(content), 0o644); err != nil {
		return textErr(fmt.Sprintf("write file failed: %v", err))
	}

	return textResult(map[string]any{
		"ok":    true,
		"path":  path,
		"bytes": len(content),
	})
}

func fileList(args map[string]any) (mcp.ToolCallResult, error) {
	path := optionalString(args, "path", ".")

	abs, errResult, err := safePath(path)
	if errResult != nil {
		return *errResult, err
	}

	entries, err := os.ReadDir(abs)
	if err != nil {
		return textErr(fmt.Sprintf("list directory failed: %v", err))
	}

	type entry struct {
		Name  string `json:"name"`
		IsDir bool   `json:"is_dir"`
		Bytes int64  `json:"bytes,omitempty"`
	}
	var files []entry
	for _, e := range entries {
		info, _ := e.Info()
		var size int64
		if info != nil && !e.IsDir() {
			size = info.Size()
		}
		files = append(files, entry{
			Name:  e.Name(),
			IsDir: e.IsDir(),
			Bytes: size,
		})
	}

	return textResult(map[string]any{
		"path":    path,
		"count":   len(files),
		"entries": files,
	})
}

func fileDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "file_read",
			Description: "Read the contents of a file from the agent's workspace. Paths are relative to the workspace root and cannot escape it.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"path": {Type: "string", Description: "Relative path to the file (e.g. \"data/report.txt\")."},
				},
				Required: []string{"path"},
			},
		},
		{
			Name:        "file_write",
			Description: "Write content to a file in the agent's workspace, creating it (and any parent directories) if it doesn't exist. Overwrites existing files.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"path":    {Type: "string", Description: "Relative path to write to (e.g. \"output/summary.txt\")."},
					"content": {Type: "string", Description: "The text content to write."},
				},
				Required: []string{"path", "content"},
			},
		},
		{
			Name:        "file_list",
			Description: "List the files and subdirectories in a directory within the agent's workspace.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"path": {Type: "string", Description: "Relative path to the directory to list. Defaults to the workspace root."},
				},
				Required: []string{},
			},
		},
	}
}
