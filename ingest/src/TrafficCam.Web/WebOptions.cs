using Npgsql;

namespace TrafficCam.Web;

/// <summary>Settings, bound from configuration: the environment variable for Database.Host is Database__Host.</summary>
public sealed class WebOptions
{
    public DatabaseOptions Database { get; init; } = new();

    /// <summary>Where the clips are. A file outside this folder is never served, whatever the database says.</summary>
    public string ClipsRoot { get; init; } = "/mnt/data/clips";
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
        // This service only reads; the server refuses anything else on its connections.
        Options = "-c default_transaction_read_only=on",
    }.ConnectionString;
}
