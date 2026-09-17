// SPDX-License-Identifier: Apache-2.0

// Developer prototype: reuse embedded OCR rules without its CLI, Git or model loop.
// Place this file under cmd/coding-agent-offline-rules in the pinned upstream checkout.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"strings"

	"github.com/alibaba/open-code-review/internal/config/rules"
	"github.com/alibaba/open-code-review/internal/delegate"
)

const source = "4e59c7e815bde045158b549cdc2a7f58a32f6ba0"

type input struct {
	SchemaVersion string   `json:"schema_version"`
	Paths         []string `json:"paths"`
}

type group struct {
	Files []string `json:"files"`
	Rule  string   `json:"rule"`
}

func run(args []string, output io.Writer) error {
	if len(args) != 1 {
		return errors.New("expected one JSON input file")
	}
	file, err := os.Open(args[0])
	if err != nil {
		return err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, 262145))
	if err != nil {
		return err
	}
	if len(data) > 262144 {
		return errors.New("input exceeds 256 KiB")
	}
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.DisallowUnknownFields()
	var request input
	if err := decoder.Decode(&request); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return errors.New("unexpected trailing JSON")
	}
	if request.SchemaVersion != "ocr.offline-paths/v1" || len(request.Paths) == 0 || len(request.Paths) > 1000 {
		return errors.New("unsupported schema or path count")
	}
	seen := make(map[string]bool)
	for _, name := range request.Paths {
		if name == "." || !fs.ValidPath(name) || strings.ContainsAny(name, "\\:\x00") || seen[name] {
			return errors.New("invalid or duplicate relative path")
		}
		for _, part := range strings.Split(strings.ToLower(name), "/") {
			switch part {
			case ".git", ".agent", ".agents", ".codex":
				return errors.New("control path is forbidden")
			}
		}
		seen[name] = true
	}
	// LoadDefault uses embedded data only. NewResolver would read host/project
	// configuration and optionally inspect source, which this probe does not authorize.
	resolver, err := rules.LoadDefault()
	if err != nil {
		return err
	}
	groups := make([]group, 0)
	for _, item := range delegate.GroupRules(resolver, request.Paths) {
		groups = append(groups, group{Files: item.Files, Rule: item.Text})
	}
	return json.NewEncoder(output).Encode(struct {
		SchemaVersion string  `json:"schema_version"`
		SourceCommit  string  `json:"source_commit"`
		Scope         string  `json:"scope"`
		Groups        []group `json:"groups"`
	}{"coding-agent.ocr-rules-probe/v1", source, "embedded-system-rules-only", groups})
}

func main() {
	if err := run(os.Args[1:], os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
