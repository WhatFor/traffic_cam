using System.Text.Json.Nodes;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

/// <summary>The contract's example payloads, with fields replaced as a test needs.</summary>
static class Examples
{
    public static string PassageJson { get; } = Read("passage.json");
    public static string EventJson { get; } = Read("event.json");
    public static string SignalChangeJson { get; } = Read("signal_change.json");

    public static byte[] Payload(params (string Name, JsonNode? Value)[] changes) => Changed(PassageJson, changes);

    public static byte[] EventPayload(params (string Name, JsonNode? Value)[] changes) => Changed(EventJson, changes);

    public static byte[] PayloadWithId(Guid id) => Payload(("id", id.ToString()));

    public static byte[] EventPayloadWithId(Guid id) => EventPayload(("id", id.ToString()));

    public static Passage Passage(Guid? id = null)
    {
        var payload = id is { } given ? PayloadWithId(given) : Payload();
        Assert.True(PassageParser.TryParse(payload, out var passage, out var error), error);
        return passage;
    }

    public static Event Event(Guid? id = null)
    {
        var payload = id is { } given ? EventPayloadWithId(given) : EventPayload();
        Assert.True(EventParser.TryParse(payload, out var @event, out var error), error);
        return @event;
    }

    public static byte[] SignalChangePayload(params (string Name, JsonNode? Value)[] changes) =>
        Changed(SignalChangeJson, changes);

    public static SignalChange SignalChange()
    {
        Assert.True(SignalChangeParser.TryParse(SignalChangePayload(), out var change, out var error), error);
        return change;
    }

    static string Read(string file) => File.ReadAllText(Path.Combine(AppContext.BaseDirectory, file));

    static byte[] Changed(string json, (string Name, JsonNode? Value)[] changes)
    {
        var node = JsonNode.Parse(json)!.AsObject();
        foreach (var (name, value) in changes)
            node[name] = value;
        return System.Text.Encoding.UTF8.GetBytes(node.ToJsonString());
    }
}
