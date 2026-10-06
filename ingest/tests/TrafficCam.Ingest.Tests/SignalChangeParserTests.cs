using TrafficCam.Contracts;
using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

public class SignalChangeParserTests
{
    [Fact]
    public void The_contract_example_parses()
    {
        Assert.True(SignalChangeParser.TryParse(Examples.SignalChangePayload(), out var change, out _));

        Assert.Equal("sh_south_primary", change.HeadId);
        Assert.Equal((SignalState.Red, SignalState.RedAmber), (change.FromState, change.ToState));
        Assert.Equal(SignalSource.Observed, change.Source);
    }

    [Fact]
    public void The_first_change_of_a_head_has_no_earlier_state() =>
        Assert.True(SignalChangeParser.TryParse(Examples.SignalChangePayload(("from_state", null)), out _, out _));

    [Theory]
    [InlineData("to_state", "purple")]
    [InlineData("source", "guessed")]
    [InlineData("schema", "event/1")]
    public void A_value_outside_the_contract_is_invalid(string field, string value) =>
        Assert.False(SignalChangeParser.TryParse(Examples.SignalChangePayload((field, value)), out _, out _));
}
