using System.Text.Json.Nodes;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

public class PassageParserTests
{
    [Fact]
    public void The_contract_example_parses()
    {
        Assert.True(PassageParser.TryParse(Examples.Payload(), out var passage, out _));

        var expected = JsonNode.Parse(Examples.PassageJson)!;
        Assert.Equal(expected["id"]!.GetValue<Guid>(), passage.Id);
        Assert.Equal(expected["camera"]!.GetValue<string>(), passage.Camera);
        Assert.Equal(RoadUserClass.Car, passage.Class);
    }

    [Fact]
    public void A_missing_field_is_invalid()
    {
        var node = JsonNode.Parse(Examples.PassageJson)!.AsObject();
        node.Remove("first_seen");

        Assert.False(PassageParser.TryParse(Bytes(node.ToJsonString()), out _, out var error));
        Assert.Contains("first_seen", error);
    }

    [Fact]
    public void Null_where_a_value_is_required_is_invalid() =>
        Assert.False(PassageParser.TryParse(Examples.Payload(("camera", null)), out _, out _));

    [Fact]
    public void Another_schema_is_invalid()
    {
        Assert.False(PassageParser.TryParse(Examples.Payload(("schema", "event/1")), out _, out var error));
        Assert.Contains("event/1", error);
    }

    [Theory]
    [InlineData("not json")]
    [InlineData("null")]
    [InlineData("[]")]
    [InlineData("")]
    public void Bytes_that_are_not_a_passage_are_invalid(string payload) =>
        Assert.False(PassageParser.TryParse(Bytes(payload), out _, out _));

    static byte[] Bytes(string text) => System.Text.Encoding.UTF8.GetBytes(text);
}
