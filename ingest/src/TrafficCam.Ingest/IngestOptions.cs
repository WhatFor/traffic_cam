using Npgsql;

namespace TrafficCam.Ingest;

/// <summary>Settings, bound from configuration: the environment variable for Mqtt.Host is Mqtt__Host.</summary>
public sealed class IngestOptions
{
    public MqttOptions Mqtt { get; init; } = new();
    public DatabaseOptions Database { get; init; } = new();

    /// <summary>How long to collect messages, after the first, before writing them together.</summary>
    public TimeSpan BatchWindow { get; init; } = TimeSpan.FromSeconds(1);
    public int BatchSize { get; init; } = 500;
    public TimeSpan MaxRetryDelay { get; init; } = TimeSpan.FromSeconds(30);
}

public sealed class MqttOptions
{
    public string Host { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 1883;
    public string Username { get; init; } = "trafficcam";
    public string Password { get; init; } = "";

    /// <summary>Fixed, because the broker keeps the session and its queued messages under this name.</summary>
    public string ClientId { get; init; } = "trafficcam-ingest";
}

public sealed class DatabaseOptions
{
    public string Host { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 5432;
    public string Name { get; init; } = "trafficcam";
    public string Username { get; init; } = "trafficcam";
    public string Password { get; init; } = "";

    public string ConnectionString => new NpgsqlConnectionStringBuilder
    {
        Host = Host,
        Port = Port,
        Database = Name,
        Username = Username,
        Password = Password,
        // Otherwise Npgsql looks for a Kerberos library, which the runtime image does not have.
        GssEncryptionMode = GssEncryptionMode.Disable,
    }.ConnectionString;
}
