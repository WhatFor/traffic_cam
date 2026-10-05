using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace TrafficCam.Contracts.Tests;

/// <summary>Every payload in contracts/examples round-trips through its generated type.</summary>
public class ExampleTests
{
    static readonly string ExamplesDir = Path.Combine(AppContext.BaseDirectory, "examples");

    static readonly Dictionary<string, Type> Types = new()
    {
        ["passage/1"] = typeof(Passage),
        ["event/1"] = typeof(Event),
        ["signal_change/1"] = typeof(SignalChange),
        ["clip/1"] = typeof(Clip),
        ["clip_command/1"] = typeof(ClipCommand),
        ["status/1"] = typeof(Status),
    };

    public static TheoryData<string> Examples() =>
        new(Directory.GetFiles(ExamplesDir, "*.json").Select(path => Path.GetFileName(path)).Order());

    [Theory]
    [MemberData(nameof(Examples))]
    public void Example_round_trips(string file)
    {
        var json = File.ReadAllText(Path.Combine(ExamplesDir, file));
        var type = Types[JsonNode.Parse(json)!["schema"]!.GetValue<string>()];

        var written = JsonSerializer.Serialize(JsonSerializer.Deserialize(json, type), type);

        Assert.True(
            JsonNode.DeepEquals(Normalise(JsonNode.Parse(json)), Normalise(JsonNode.Parse(written))),
            $"expected {json}\nwritten {written}");
    }

    /// <summary>Rewrites timestamps as UTC ticks, since "Z" and "+00:00" are the same instant.</summary>
    static JsonNode? Normalise(JsonNode? node)
    {
        switch (node)
        {
            case JsonObject obj:
                foreach (var (key, value) in obj.ToList())
                    obj[key] = Normalise(value?.DeepClone());
                return obj;
            case JsonValue value when value.TryGetValue<string>(out var text)
                && text.Contains('T')
                && DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var instant):
                return JsonValue.Create(instant.UtcTicks);
            default:
                return node;
        }
    }
}
