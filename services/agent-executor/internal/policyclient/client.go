package policyclient

import (
	"bytes"
	"encoding/json"
	"net/http"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func New(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		http: &http.Client{
			Timeout: 2 * time.Second,
		},
	}
}

type PolicyRequest struct {
	Step struct {
		Name string `json:"name"`
	} `json:"step"`

	Budget struct {
		Total     float64 `json:"total"`
		Remaining float64 `json:"remaining"`
	} `json:"budget"`

	Request struct {
		LatencySLAMs int `json:"latency_sla_ms"`
	} `json:"request"`
}

type PolicyResponse struct {
	Decision struct {
		Allowed           bool   `json:"allowed"`
		SelectedModelTier string `json:"selected_model_tier"`
		HardStop          bool   `json:"hard_stop"`
	} `json:"decision"`
	Reason string `json:"reason"`
}

func (c *Client) Evaluate(req PolicyRequest) (*PolicyResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequest(
		http.MethodPost,
		c.baseURL+"/policy/evaluate",
		bytes.NewReader(body),
	)
	if err != nil {
		return nil, err
	}

	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var result PolicyResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return &result, nil
}
