using System.Text.Json.Nodes;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

static class Examples
{
    public static string PassageJson { get; } =
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "passage.json"));

    /// <summary>The contract's example passage, with the given fields replaced.</summary>
    public static byte[] Payload(params (string Name, JsonNode? Value)[] changes)
    {
        var node = JsonNode.Parse(PassageJson)!.AsObject();
        foreach (var (name, value) in changes)
            node[name] = value;
        return System.Text.Encoding.UTF8.GetBytes(node.ToJsonString());
    }

    public static byte[] PayloadWithId(Guid id) => Payload(("id", id.ToString()));

    public static Passage Passage(Guid? id = null)
    {
        var payload = id is { } given ? PayloadWithId(given) : Payload();
        Assert.True(PassageParser.TryParse(payload, out var passage, out var error), error);
        return passage;
    }
}
