using Npgsql;
using Prometheus;
using TrafficCam.Ingest;
using TrafficCam.Ingest.Database;
using TrafficCam.Ingest.Mqtt;

// The runtime image has no HTTP client, so the container's health check runs this program.
if (args.Contains("--healthcheck"))
{
    var port = Environment.GetEnvironmentVariable("ASPNETCORE_HTTP_PORTS") ?? "8080";
    using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
    try
    {
        using var response = await http.GetAsync($"http://127.0.0.1:{port}/healthz");
        return response.IsSuccessStatusCode ? 0 : 1;
    }
    catch (HttpRequestException)
    {
        return 1;
    }
}

var builder = WebApplication.CreateBuilder(args);
builder.Logging.AddSimpleConsole(console => console.SingleLine = true);
// Otherwise every scrape and health check is logged.
builder.Logging.AddFilter("Microsoft.AspNetCore", LogLevel.Warning);
var options = builder.Configuration.Get<IngestOptions>() ?? new IngestOptions();
builder.Services.AddSingleton(options);
builder.Services.AddSingleton(new IngestMetrics());
builder.Services.AddSingleton(NpgsqlDataSource.Create(options.Database.ConnectionString));
builder.Services.AddSingleton<RecordStore>();
builder.Services.AddSingleton<Consumer>();
builder.Services.AddHostedService(services => services.GetRequiredService<Consumer>());

var app = builder.Build();
// Before the consumer starts, so nothing is read until there is a table to put it in.
Migrator.Apply(options.Database.ConnectionString, app.Services.GetRequiredService<ILoggerFactory>());

app.MapMetrics();
app.MapGet("/healthz", (Consumer consumer) =>
    consumer.Connected ? Results.Ok("ok") : Results.StatusCode(StatusCodes.Status503ServiceUnavailable));
app.Run();
return 0;
