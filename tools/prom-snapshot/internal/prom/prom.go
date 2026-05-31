// Package prom is a stdlib-only Prometheus HTTP API client. It only
// implements the two endpoints prom-snapshot needs:
//
//	GET /api/v1/query        ?query=...&time=...
//	GET /api/v1/query_range  ?query=...&start=...&end=...&step=...
//
// We avoid pulling github.com/prometheus/client_golang's API package
// just to issue two HTTP GETs.
package prom

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(baseURL string) *Client {
	return &Client{
		BaseURL: baseURL,
		HTTP:    &http.Client{Timeout: 30 * time.Second},
	}
}

// Sample is one (timestamp, value) pair from a vector or matrix result.
type Sample struct {
	T time.Time
	V float64
}

// Series is a labelled stream of samples.
type Series struct {
	Metric  map[string]string
	Samples []Sample
}

type apiResp struct {
	Status string `json:"status"`
	Data   struct {
		ResultType string            `json:"resultType"`
		Result     []json.RawMessage `json:"result"`
	} `json:"data"`
	ErrorType string `json:"errorType,omitempty"`
	Error     string `json:"error,omitempty"`
}

// Query runs an instant query at the given time. Returns one Series per
// label-set in the result vector; each Series has exactly one Sample.
func (c *Client) Query(ctx context.Context, expr string, at time.Time) ([]Series, error) {
	q := url.Values{}
	q.Set("query", expr)
	q.Set("time", fmt.Sprintf("%d", at.Unix()))
	return c.do(ctx, "/api/v1/query", q, false)
}

// QueryRange runs a range query.
func (c *Client) QueryRange(ctx context.Context, expr string, start, end time.Time, step time.Duration) ([]Series, error) {
	q := url.Values{}
	q.Set("query", expr)
	q.Set("start", fmt.Sprintf("%d", start.Unix()))
	q.Set("end", fmt.Sprintf("%d", end.Unix()))
	q.Set("step", fmt.Sprintf("%ds", int(step.Seconds())))
	return c.do(ctx, "/api/v1/query_range", q, true)
}

func (c *Client) do(ctx context.Context, path string, q url.Values, matrix bool) ([]Series, error) {
	u := c.BaseURL + path + "?" + q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var r apiResp
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	if r.Status != "success" {
		return nil, fmt.Errorf("prometheus error %s: %s", r.ErrorType, r.Error)
	}
	out := make([]Series, 0, len(r.Data.Result))
	for _, raw := range r.Data.Result {
		s, err := decodeSeries(raw, matrix)
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, nil
}

// vector result element: { metric: {...}, value: [ts, "x"] }
// matrix result element: { metric: {...}, values: [[ts, "x"], ...] }
type rawSeries struct {
	Metric map[string]string `json:"metric"`
	Value  []any             `json:"value"`
	Values [][]any           `json:"values"`
}

func decodeSeries(raw json.RawMessage, matrix bool) (Series, error) {
	var rs rawSeries
	if err := json.Unmarshal(raw, &rs); err != nil {
		return Series{}, err
	}
	s := Series{Metric: rs.Metric}
	if matrix {
		for _, v := range rs.Values {
			smp, err := decodeSample(v)
			if err != nil {
				return Series{}, err
			}
			s.Samples = append(s.Samples, smp)
		}
	} else if len(rs.Value) > 0 {
		smp, err := decodeSample(rs.Value)
		if err != nil {
			return Series{}, err
		}
		s.Samples = append(s.Samples, smp)
	}
	return s, nil
}

func decodeSample(pair []any) (Sample, error) {
	if len(pair) != 2 {
		return Sample{}, fmt.Errorf("malformed sample: %v", pair)
	}
	tsF, ok := pair[0].(float64)
	if !ok {
		return Sample{}, fmt.Errorf("non-float timestamp: %v", pair[0])
	}
	valS, ok := pair[1].(string)
	if !ok {
		return Sample{}, fmt.Errorf("non-string value: %v", pair[1])
	}
	v, err := strconv.ParseFloat(valS, 64)
	if err != nil {
		// Prometheus uses "NaN" / "+Inf" / "-Inf" for these — treat as NaN.
		v = 0
	}
	return Sample{T: time.Unix(int64(tsF), 0), V: v}, nil
}

// FirstScalar is a convenience for instant queries that return one
// label-less series. Returns 0 and no error if the result set is empty.
func FirstScalar(s []Series) float64 {
	if len(s) == 0 || len(s[0].Samples) == 0 {
		return 0
	}
	return s[0].Samples[0].V
}
