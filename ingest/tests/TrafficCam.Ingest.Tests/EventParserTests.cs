using System.Text.Json.Nodes;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

public class EventParserTests
{
    [Fact]
    public void The_contract_example_parses()
    {
        Assert.True(EventParser.TryParse(Examples.EventPayload(), out var parsed, out _));

        var expected = JsonNode.Parse(Examples.EventJson)!;
        Assert.Equal(expected["id"]!.GetValue<Guid>(), parsed.Id);
        Assert.Equal("box_junction_stop", parsed.Type);
        Assert.Equal(RoadUserClass.Car, parsed.Class);
    }

    [Fact]
    public void A_missing_field_is_invalid()
    {
        var node = JsonNode.Parse(Examples.EventJson)!.AsObject();
        node.Remove("attrs");

        Assert.False(EventParser.TryParse(System.Text.Encoding.UTF8.GetBytes(node.ToJsonString()), out _, out var error));
        Assert.Contains("attrs", error);
    }

    [Fact]
    public void A_passage_is_not_an_event() =>
        Assert.False(EventParser.TryParse(Examples.Payload(), out _, out _));

    [Theory]
    [InlineData("Box Junction")]
    [InlineData("box/junction")]
    [InlineData("")]
    public void A_type_outside_the_contracts_pattern_is_invalid(string type)
    {
        Assert.False(EventParser.TryParse(Examples.EventPayload(("type", type)), out _, out var error));
        Assert.Contains("type", error);
    }
}
