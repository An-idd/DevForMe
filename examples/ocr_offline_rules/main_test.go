// SPDX-License-Identifier: Apache-2.0

package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestRejectInvalidInput(t *testing.T) {
	for _, data := range []string{
		`{"schema_version":"other","paths":["a.py"]}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":["../secret"]}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":[".GIT/config"]}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":["C:/secret"]}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":["a.py","a.py"]}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":["a.py"],"extra":true}`,
		`{"schema_version":"ocr.offline-paths/v1","paths":["a.py"]}{}`,
	} {
		name := filepath.Join(t.TempDir(), "input.json")
		if err := os.WriteFile(name, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		var out bytes.Buffer
		if err := run([]string{name}, &out); err == nil || out.Len() != 0 {
			t.Fatalf("invalid input produced a result: %s", data)
		}
	}
}

func TestReturnAllRequestedRules(t *testing.T) {
	name := filepath.Join(t.TempDir(), "input.json")
	data := `{"schema_version":"ocr.offline-paths/v1","paths":["sample.py","new.py","README.md"]}`
	if err := os.WriteFile(name, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if err := run([]string{name}, &out); err != nil {
		t.Fatal(err)
	}
	var result struct {
		SchemaVersion string
		Groups        []group
	}
	// Read the versioned wire name explicitly, rather than Go field-name matching.
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(envelope["schema_version"], &result.SchemaVersion); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(envelope["groups"], &result.Groups); err != nil {
		t.Fatal(err)
	}
	found := make(map[string]string)
	for _, item := range result.Groups {
		for _, path := range item.Files {
			found[path] = item.Rule
		}
	}
	if result.SchemaVersion != "coding-agent.ocr-rules-probe/v1" || len(found) != 3 ||
		found["sample.py"] == "" || found["sample.py"] != found["new.py"] || found["README.md"] == "" {
		t.Fatal("rule coverage or grouping was lost")
	}
}
