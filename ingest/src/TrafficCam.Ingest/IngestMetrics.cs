using Prometheus;

namespace TrafficCam.Ingest;

public sealed class IngestMetrics
{
    readonly Counter messages;

    /// <param name="registry">A registry of its own for tests; the default one is what /metrics serves.</param>
    public IngestMetrics(CollectorRegistry? registry = null)
    {
        var factory = registry is null ? Metrics.DefaultFactory : Metrics.WithCustomRegistry(registry);
        messages = factory.CreateCounter(
            "trafficcam_ingest_messages_total",
            "Messages handled, by kind of record and what became of them.",
            "kind",
            "result");
        InsertSeconds = factory.CreateHistogram(
            "trafficcam_ingest_insert_seconds",
            "Time to write one batch, including the commit.",
            new HistogramConfiguration { Buckets = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10] });
        DatabaseErrors = factory.CreateCounter(
            "trafficcam_ingest_database_errors_total", "Failed attempts to write a batch.");
        MqttConnected = factory.CreateGauge(
            "trafficcam_ingest_mqtt_connected", "1 if connected to the MQTT broker, else 0.");
        LastStored = factory.CreateGauge(
            "trafficcam_ingest_last_stored_timestamp_seconds", "When a batch was last committed.");
        foreach (var kind in new[] { Passage, Event, Signal })
        {
            foreach (var counter in new[] { Stored(kind), Duplicate(kind), Invalid(kind) })
                counter.Inc(0);
        }
    }

    public const string Passage = "passage";
    public const string Event = "event";
    public const string Signal = "signal";

    public Counter.Child Stored(string kind) => messages.WithLabels(kind, "stored");
    public Counter.Child Duplicate(string kind) => messages.WithLabels(kind, "duplicate");
    public Counter.Child Invalid(string kind) => messages.WithLabels(kind, "invalid");
    public Histogram InsertSeconds { get; }
    public Counter DatabaseErrors { get; }
    public Gauge MqttConnected { get; }
    public Gauge LastStored { get; }
}
