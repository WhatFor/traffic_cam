using Xunit;

namespace TrafficCam.Web.Tests;

public class WordingTests
{
    [Theory]
    [InlineData("red_light", "Red light")]
    [InlineData("box_junction_stop", "Box junction stop")]
    [InlineData("manual", "Manual")]
    public void A_type_is_titled_in_words(string type, string title) => Assert.Equal(title, Wording.Title(type));

    [Theory]
    [InlineData("red_light", """{"movement": "west->north", "time_into_red_s": 1.23}""", "west->north, 1.2 s into red")]
    [InlineData("red_light", """{"movement": null, "time_into_red_s": 3.03}""", "3 s into red")]
    [InlineData("amber_crossing", """{"movement": "west->east", "time_into_amber_s": 0.17}""", "west->east, 0.2 s into amber")]
    [InlineData("box_junction_stop", """{"movement": "north->west", "stationary_s": 6.2, "x": 1100}""", "north->west, stood 6.2 s")]
    [InlineData("banned_turn", """{"movement": "south->west", "turn": "left_from_south", "signals": {}}""", "south->west")]
    [InlineData("speeding", """{"movement": "west->east", "speed_mph": 41.0, "speed_kmh": 66.0, "limit_mph": 30}""", "west->east, 41 mph")]
    [InlineData("near_miss", """{"pet_s": 0.8, "movements": ["west->south", null], "tracks": [1, 2], "speeds_mph": [12.1, 20.5]}""", "west->south and unknown, 0.8 s apart")]
    [InlineData("incident_candidate", """{"signs": ["contact", "sudden_stop", "standstill"], "pet_s": 0.2, "tracks": [1, 2]}""", "contact, sudden stop, standstill, 0.2 s apart")]
    [InlineData("incident_candidate", """{"signs": ["lone_standstill"], "standing_s": 60, "tracks": [7]}""", "lone standstill, stood 60 s")]
    [InlineData("something_new", "{}", null)]
    public void An_events_detail_is_its_movement_and_its_measure(string type, string attrs, string? detail) =>
        Assert.Equal(detail, Wording.Detail(type, reason: null, attrs));

    [Fact]
    public void A_manual_triggers_detail_is_the_reason_given()
    {
        Assert.Equal("to see", Wording.Detail(Wording.Manual, "to see", attrs: null));
        Assert.Null(Wording.Detail("red_light", reason: null, attrs: null));
    }

    [Theory]
    [InlineData(19.6, "20 s")]
    [InlineData(60.4, "1 min")]
    [InlineData(60.9, "1 min 1 s")]
    [InlineData(0.2, "0 s")]
    public void A_length_reads_in_seconds_then_minutes(double seconds, string text) =>
        Assert.Equal(text, Wording.Length(seconds));

    [Theory]
    [InlineData("/mnt/data/clips/2026/10/06/a.mp4", "/mnt/data/clips/2026/10/06/a.mp4")]
    [InlineData("/mnt/data/clips/../postgres/a.mp4", null)]
    [InlineData("/mnt/data/clips-other/a.mp4", null)]
    [InlineData("/etc/passwd", null)]
    public void A_path_is_followed_only_inside_the_folder(string path, string? allowed)
    {
        Assert.Equal(allowed, ClipFiles.Within("/mnt/data/clips", path));
        Assert.Equal(allowed, ClipFiles.Within("/mnt/data/clips/", path));
    }
}
