// prom-snapshot queries Prometheus for one experiment-run's window
// and writes one CSV file per metric family. Output goes to a
// directory (default: derived from --experiment + --run-id) or to
// stdout if --out=-.
//
// See yass-docs/observability-v2-spec.md §G5.
package main

import (
	"context"
	"encoding/csv"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/duobitx/yass-experiments/tools/prom-snapshot/internal/prom"
)

// Default metric families to capture — the headline set from the v2
// spec. Authors can override with --metrics csv if they want more.
var defaultMetrics = []string{
	"yass_file_produced_total",
	"yass_file_produced_bytes_total",
	"yass_file_received_total",
	"yass_file_received_bytes_total",
	"yass_file_lost_total",
	"yass_file_delivery_seconds_bucket",
	"yass_file_delivery_seconds_count",
	"yass_file_delivery_seconds_sum",
	"yass_battery_wh",
	"yass_battery_capacity_wh",
	"yass_battery_consumed_wh_total",
	"yass_in_shadow",
	"yass_low_power",
	"yass_volume_used_bytes",
	"yass_volume_capacity_bytes",
	"yass_container_cpu_millicores",
	"yass_container_memory_bytes",
	"yass_network_tx_bytes_total",
	"yass_network_rx_bytes_total",
	"yass_hardware_event_active",
	"yass_hardware_event_dropped_total",
	"yass_los_active",
}

func main() {
	var (
		promURL    = flag.String("prometheus", "http://prometheus.yass-system.svc:9090", "Prometheus base URL")
		experiment = flag.String("experiment", "", "experiment label to filter (required)")
		runID      = flag.String("run-id", "", "run_id label to filter (recommended)")
		engine     = flag.String("engine", "", "engine label to filter (optional)")
		from       = flag.String("from", "", "window start (RFC3339); default: --to - --window")
		to         = flag.String("to", "", "window end (RFC3339); default: now")
		windowFlag = flag.Duration("window", 1*time.Hour, "window length when --from is not set")
		step       = flag.Duration("step", 15*time.Second, "Prometheus query_range step")
		out        = flag.String("out", "", "output directory (default <experiment>-<runId>-csv); '-' for stdout (tar.gz)")
		metricsCsv = flag.String("metrics", "", "comma-separated metric family list (overrides defaults)")
	)
	flag.Parse()

	if *experiment == "" {
		fmt.Fprintln(os.Stderr, "prom-snapshot: --experiment is required")
		os.Exit(2)
	}

	winTo := time.Now()
	if *to != "" {
		t, err := time.Parse(time.RFC3339, *to)
		if err != nil {
			log.Fatalf("bad --to: %v", err)
		}
		winTo = t
	}
	winFrom := winTo.Add(-*windowFlag)
	if *from != "" {
		t, err := time.Parse(time.RFC3339, *from)
		if err != nil {
			log.Fatalf("bad --from: %v", err)
		}
		winFrom = t
	}

	metricList := defaultMetrics
	if *metricsCsv != "" {
		metricList = strings.Split(*metricsCsv, ",")
	}

	dir := *out
	if dir == "" {
		name := *experiment
		if *runID != "" {
			name = name + "-" + *runID
		}
		dir = name + "-csv"
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		log.Fatalf("mkdir %s: %v", dir, err)
	}

	client := prom.New(*promURL)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	for _, m := range metricList {
		expr := buildSelector(m, *experiment, *engine, *runID)
		series, err := client.QueryRange(ctx, expr, winFrom, winTo, *step)
		if err != nil {
			log.Printf("query %s: %v (skipping)", m, err)
			continue
		}
		path := filepath.Join(dir, m+".csv")
		if err := writeSeriesCSV(path, series); err != nil {
			log.Printf("write %s: %v (skipping)", path, err)
			continue
		}
		fmt.Printf("wrote %s (%d series)\n", path, len(series))
	}
	fmt.Printf("done — output in %s\n", dir)
}

// buildSelector wraps a metric name with the standard experiment-run label
// filters, then de-duplicates exporter copies. Several metrics-bridge / mqtt2prom
// pods (and peer-IP churn during a long run) re-emit the SAME logical series under
// different instance/pod/peer labels, so a raw snapshot holds N near-identical
// copies. `max without (instance, pod, peer)` keeps every identity label (fsNode,
// peer_node, container, le, source/target_fsNode, ...) and folds only those volatile
// ones, taking the max (counters -> the most-complete exporter instance). The
// experiment/run_id filter already prevents any cross-experiment mixing.
func buildSelector(metric, experiment, engine, runID string) string {
	parts := []string{fmt.Sprintf(`experiment="%s"`, experiment)}
	if engine != "" {
		parts = append(parts, fmt.Sprintf(`engine="%s"`, engine))
	}
	if runID != "" {
		parts = append(parts, fmt.Sprintf(`run_id="%s"`, runID))
	}
	return fmt.Sprintf("max without (instance, pod, peer) (%s{%s})", metric, strings.Join(parts, ","))
}

// writeSeriesCSV writes one Prometheus QueryRange result as a CSV
// matrix: columns = sorted timestamps; rows = sorted labelsets.
func writeSeriesCSV(path string, series []prom.Series) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()

	// Collect unique timestamps across all series, sorted.
	tsSet := map[time.Time]struct{}{}
	for _, s := range series {
		for _, sm := range s.Samples {
			tsSet[sm.T] = struct{}{}
		}
	}
	ts := make([]time.Time, 0, len(tsSet))
	for t := range tsSet {
		ts = append(ts, t)
	}
	sort.Slice(ts, func(i, j int) bool { return ts[i].Before(ts[j]) })

	// Collect all label keys.
	labelKeys := map[string]struct{}{}
	for _, s := range series {
		for k := range s.Metric {
			labelKeys[k] = struct{}{}
		}
	}
	keys := make([]string, 0, len(labelKeys))
	for k := range labelKeys {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	// Header: label columns + timestamp columns.
	header := append([]string{}, keys...)
	for _, t := range ts {
		header = append(header, t.UTC().Format(time.RFC3339))
	}
	if err := w.Write(header); err != nil {
		return err
	}

	// Index timestamps for fast lookup per series.
	tsIdx := map[time.Time]int{}
	for i, t := range ts {
		tsIdx[t] = i
	}

	// Sort series for deterministic output.
	sort.Slice(series, func(i, j int) bool {
		return labelKey(series[i].Metric) < labelKey(series[j].Metric)
	})
	for _, s := range series {
		row := make([]string, len(keys)+len(ts))
		for i, k := range keys {
			row[i] = s.Metric[k]
		}
		values := make([]string, len(ts))
		for _, sm := range s.Samples {
			i, ok := tsIdx[sm.T]
			if !ok {
				continue
			}
			values[i] = strconv.FormatFloat(sm.V, 'f', -1, 64)
		}
		copy(row[len(keys):], values)
		if err := w.Write(row); err != nil {
			return err
		}
	}
	return nil
}

func labelKey(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k)
		b.WriteString("=")
		b.WriteString(m[k])
		b.WriteString(";")
	}
	return b.String()
}
