using TrafficCam.Ingest.Messages;
using Xunit;

namespace TrafficCam.Ingest.Tests;

public class ClipParserTests
{
    [Fact]
    public void The_contract_example_parses_with_everything_it_was_recorded_for()
    {
        Assert.True(ClipParser.TryParse(Examples.ClipPayload(), out var clip, out _));

        Assert.Equal(["red_light", "manual"], clip.Triggers.Select(trigger => trigger.Type));
        Assert.Equal([null, "manual test"], clip.Triggers.Select(trigger => trigger.Reason));
        Assert.Equal(clip.EventId, clip.Triggers.First().EventId);
        Assert.Equal(45120334, clip.Bytes);
    }

    [Fact]
    public void A_clip_may_have_no_still_frame() =>
        Assert.True(ClipParser.TryParse(Examples.ClipPayload(("keyframe_path", null)), out _, out _));

    [Theory]
    [InlineData("triggers")]
    [InlineData("path")]
    [InlineData("started_at")]
    public void A_clip_without_a_required_field_is_invalid(string field) =>
        Assert.False(ClipParser.TryParse(Examples.ClipPayload((field, null)), out _, out _));

    [Fact]
    public void The_deletion_example_parses_and_is_not_a_clip()
    {
        Assert.True(ClipDeletedParser.TryParse(Examples.ClipDeletedPayload(), out var deleted, out _));

        Assert.Equal(Examples.Clip().Id, deleted.Id);
        Assert.False(ClipParser.TryParse(Examples.ClipDeletedPayload(), out _, out _));
        Assert.False(ClipDeletedParser.TryParse(Examples.ClipPayload(), out _, out _));
    }
}
