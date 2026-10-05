using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using TrafficCam.Ingest.Database;
using Xunit;

namespace TrafficCam.Ingest.Tests;

/// <summary>Real PostgreSQL and Mosquitto processes from the devShell, in a temp directory.</summary>
public sealed class Servers : IAsyncLifetime
{
    readonly string directory = Directory.CreateTempSubdirectory("trafficcam-ingest-").FullName;
    readonly string? postgres = Find("postgres");
    readonly string? initdb = Find("initdb");
    readonly string? pgCtl = Find("pg_ctl");
    readonly string? mosquitto = Find("mosquitto");
    Process? broker;

    public bool Available => postgres is not null && initdb is not null && pgCtl is not null && mosquitto is not null;
    string Data => Path.Combine(directory, "data");
    public int DatabasePort { get; } = FreePort();
    public int BrokerPort { get; } = FreePort();

    public async ValueTask InitializeAsync()
    {
        if (!Available)
            return;
        await RunAsync(initdb!, "-D", Data, "-U", "trafficcam", "-A", "trust", "--no-sync");
        await StartDatabaseAsync();

        var config = Path.Combine(directory, "mosquitto.conf");
        await File.WriteAllTextAsync(config, $"listener {BrokerPort} 127.0.0.1\nallow_anonymous true\n");
        broker = Start(mosquitto!, "-c", config);
        await WaitForPortAsync(BrokerPort);
    }

    public async ValueTask DisposeAsync()
    {
        if (!Available)
            return;
        await StopDatabaseAsync();
        broker?.Kill();
        broker?.Dispose();
        Directory.Delete(directory, recursive: true);
    }

    public Task StartDatabaseAsync() =>
        // pg_ctl returns once the server accepts connections.
        RunAsync(
            pgCtl!, "start", "-w", "-D", Data, "-l", Path.Combine(directory, "postgres.log"),
            "-o", $"-p {DatabasePort} -c listen_addresses=127.0.0.1 -c unix_socket_directories= "
                + "-c shared_preload_libraries=timescaledb -c timescaledb.telemetry_level=off -c fsync=off");

    public Task StopDatabaseAsync() => RunAsync(pgCtl!, "stop", "-w", "-m", "fast", "-D", Data);

    /// <summary>A new, migrated database, so tests do not see each other's rows.</summary>
    public async Task<DatabaseOptions> CreateDatabaseAsync()
    {
        var name = "test_" + Guid.NewGuid().ToString("N");
        // Not pooled: a pooled connection from before a restart of the server would be dead.
        await using (var connection = new NpgsqlConnection(Options("postgres").ConnectionString + ";Pooling=false"))
        {
            await connection.OpenAsync();
            await using var create = new NpgsqlCommand($"CREATE DATABASE {name}", connection);
            await create.ExecuteNonQueryAsync();
        }
        var options = Options(name);
        Migrator.Apply(options.ConnectionString, NullLoggerFactory.Instance);
        return options;
    }

    DatabaseOptions Options(string name) =>
        new() { Port = DatabasePort, Name = name, Username = "trafficcam" };

    static Process Start(string file, params string[] arguments)
    {
        var start = new ProcessStartInfo(file, arguments)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        var process = Process.Start(start)!;
        // Drained, or a chatty server fills the pipe and stops.
        process.OutputDataReceived += (_, _) => { };
        process.ErrorDataReceived += (_, _) => { };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        return process;
    }

    static async Task RunAsync(string file, params string[] arguments)
    {
        using var process = Start(file, arguments);
        await process.WaitForExitAsync();
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"{Path.GetFileName(file)} {arguments[0]} exited with {process.ExitCode}");
    }

    static async Task WaitForPortAsync(int port)
    {
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (true)
        {
            try
            {
                using var probe = new TcpClient();
                await probe.ConnectAsync(IPAddress.Loopback, port);
                return;
            }
            catch (SocketException) when (DateTime.UtcNow < deadline)
            {
                await Task.Delay(50);
            }
        }
    }

    static int FreePort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    static string? Find(string program) =>
        (Environment.GetEnvironmentVariable("PATH") ?? "")
            .Split(Path.PathSeparator)
            .Select(directory => Path.Combine(directory, program))
            .FirstOrDefault(File.Exists);
}

[CollectionDefinition(Name)]
public sealed class ServersCollection : ICollectionFixture<Servers>
{
    public const string Name = "servers";
}
