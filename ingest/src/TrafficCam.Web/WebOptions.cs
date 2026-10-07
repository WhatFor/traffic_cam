using Npgsql;

namespace TrafficCam.Web;

/// <summary>Settings, bound from configuration: the environment variable for Database.Host is Database__Host.</summary>
public sealed class WebOptions
{
    public DatabaseOptions Database { get; init; } = new();
    public MqttOptions Mqtt { get; init; } = new();

    /// <summary>Where the clips are. A file outside this folder is never served, whatever the database says.</summary>
    public string ClipsRoot { get; init; } = "/mnt/data/clips";

    /// <summary>The camera whose vision service is asked for clips: `camera.id` in site.yaml.</summary>
    public string Camera { get; init; } = "";

    /// <summary>How far back a quick clip reaches. Vision gives what its buffer holds if that is less.</summary>
    public double QuickClipSeconds { get; init; } = 90;
}

public sealed class MqttOptions
{
    public string Host { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 1883;
    public string Username { get; init; } = "trafficcam";
    public string Password { get; init; } = "";
}

public sealed class DatabaseOptions
{
    public string Host { get; init; } = "127.0.0.1";
    public int Port { get; init; } = 5432;
    public string Name { get; init; } = "trafficcam";
    public string Username { get; init; } = "trafficcam";
    public string Password { get; init; } = "";

    /// <summary>For the pages, which only read; the server refuses anything else on these connections.</summary>
    public string ReadOnlyConnectionString =>
        new NpgsqlConnectionStringBuilder(ConnectionString) { Options = "-c default_transaction_read_only=on" }
            .ConnectionString;

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
