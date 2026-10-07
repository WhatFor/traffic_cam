using System.Net;
using System.Net.Http.Headers;
using System.Text.RegularExpressions;
using TrafficCam.Ingest.Tests;
using TrafficCam.Web.Pages;
using Xunit;

namespace TrafficCam.Web.Tests;

[Collection(ServersCollection.Name)]
public class SiteTests(Servers servers)
{
    static readonly DateTimeOffset Noon = new(2026, 10, 6, 12, 0, 0, TimeSpan.Zero);
    const string RedLightAttrs = """{"line": "stopline_west_northbound", "movement": "west->north", "time_into_red_s": 1.23}""";

    [Fact]
    public async Task The_index_has_a_column_for_each_type_with_the_newest_first()
    {
        await using var site = await Site.StartAsync(servers);
        var early = await site.AddClipAsync(Noon, [new Trigger("red_light", 5, Attrs: RedLightAttrs)]);
        var late = await site.AddClipAsync(
            Noon.AddHours(1), [new Trigger("red_light", 5), new Trigger("manual", 9, Reason: "to see")]);
        var gone = await site.AddClipAsync(Noon.AddHours(2), [new Trigger("banned_turn", 5)]);
        await site.DeleteAsync(gone);

        var html = await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken);

        Assert.DoesNotContain(gone.ToString(), html);
        Assert.DoesNotContain("Banned turn", html);
        var redLight = Column(html, "Red light");
        Assert.True(redLight.IndexOf(late.ToString()) < redLight.IndexOf(early.ToString()), "newest first");
        Assert.Contains("west-&gt;north, 1.2 s into red", redLight);
        Assert.Contains("All 2", redLight);
        // A clip recorded for two things is under both. An event's link opens it just before the
        // moment; a manual clip's moment is when it was asked for, so that link opens it at the start.
        var manual = Column(html, "Manual");
        Assert.Contains($"/clips/{late}?t=0", manual);
        Assert.Contains("to see", manual);
        Assert.DoesNotContain(early.ToString(), manual);
        Assert.Contains($"/clips/{late}?t=3", redLight);
    }

    [Fact]
    public async Task With_no_clips_the_index_says_so()
    {
        await using var site = await Site.StartAsync(servers);

        Assert.Contains("No clips yet", await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task A_types_list_goes_on_to_older_clips()
    {
        await using var site = await Site.StartAsync(servers);
        var oldest = await site.AddClipAsync(Noon, [new Trigger("box_junction_stop", 5)]);
        var newest = oldest;
        for (var minute = 1; minute <= TypeModel.PageSize; minute++)
            newest = await site.AddClipAsync(Noon.AddMinutes(minute), [new Trigger("box_junction_stop", 5)]);
        await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);

        var first = await site.Client.GetStringAsync("/types/box_junction_stop", TestContext.Current.CancellationToken);

        Assert.Equal(TypeModel.PageSize, Regex.Count(first, "<li>"));
        Assert.Contains(newest.ToString(), first);
        Assert.DoesNotContain(oldest.ToString(), first);
        var older = WebUtility.HtmlDecode(Regex.Match(first, "href=\"(/types/[^\"]*before=[^\"]*)\"").Groups[1].Value);
        var second = await site.Client.GetStringAsync(older, TestContext.Current.CancellationToken);
        Assert.Equal(1, Regex.Count(second, "<li>"));
        Assert.Contains(oldest.ToString(), second);
        Assert.DoesNotContain("Older", second);
    }

    [Fact]
    public async Task A_clips_page_plays_it_and_says_what_it_shows()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(
            Noon, [new Trigger("red_light", 5, Attrs: RedLightAttrs), new Trigger("manual", 9.5, Reason: "to see")]);

        var html = await site.Client.GetStringAsync($"/clips/{id}?t=3", TestContext.Current.CancellationToken);

        Assert.Contains($"src=\"/clips/{id}/video#t=3\"", html);
        Assert.Contains($"poster=\"/clips/{id}/still\"", html);
        Assert.Contains("20 s, 0 MB", html);
        Assert.Matches("Red light.*data-seek=\"5\".*west-&gt;north, 1.2 s into red.*Manual.*data-seek=\"9.5\".*to see",
            html.ReplaceLineEndings(" "));
        Assert.Contains("2026-10-06T12:00:05", html);
    }

    [Fact]
    public async Task The_video_is_served_whole_or_in_the_part_asked_for()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("manual", 5, Reason: "to see")]);

        using var whole = await site.Client.GetAsync($"/clips/{id}/video", TestContext.Current.CancellationToken);
        using var request = new HttpRequestMessage(HttpMethod.Get, $"/clips/{id}/video")
        {
            Headers = { Range = new RangeHeaderValue(100, 199) },
        };
        using var part = await site.Client.SendAsync(request, TestContext.Current.CancellationToken);
        using var still = await site.Client.GetAsync($"/clips/{id}/still", TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, whole.StatusCode);
        Assert.Equal("video/mp4", whole.Content.Headers.ContentType?.MediaType);
        Assert.Equal("bytes", whole.Headers.AcceptRanges.Single());
        Assert.Equal(Site.Video, await whole.Content.ReadAsByteArrayAsync(TestContext.Current.CancellationToken));
        Assert.Equal(HttpStatusCode.PartialContent, part.StatusCode);
        Assert.Equal(new ContentRangeHeaderValue(100, 199, Site.Video.Length), part.Content.Headers.ContentRange);
        Assert.Equal(Site.Video[100..200], await part.Content.ReadAsByteArrayAsync(TestContext.Current.CancellationToken));
        Assert.Equal("image/jpeg", still.Content.Headers.ContentType?.MediaType);
        Assert.True(whole.Headers.CacheControl?.Extensions.Any(extension => extension.Name == "immutable"));
    }

    [Fact]
    public async Task A_deleted_clip_has_a_page_saying_so_and_no_files()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);
        await site.DeleteAsync(id);

        using var page = await site.Client.GetAsync($"/clips/{id}", TestContext.Current.CancellationToken);
        var html = await page.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.NotFound, page.StatusCode);
        Assert.Contains("This clip was deleted", html);
        Assert.DoesNotContain("<video", html);
        Assert.Equal(HttpStatusCode.NotFound, await StatusAsync(site, $"/clips/{id}/video"));
        Assert.Equal(HttpStatusCode.NotFound, await StatusAsync(site, $"/clips/{id}/still"));
    }

    [Fact]
    public async Task Only_a_file_inside_the_clips_folder_is_served()
    {
        await using var site = await Site.StartAsync(servers);
        var outside = Path.Combine(Path.GetTempPath(), $"outside-{Guid.NewGuid()}.mp4");
        await File.WriteAllBytesAsync(outside, Site.Video, TestContext.Current.CancellationToken);
        try
        {
            Trigger[] manual = [new Trigger("manual", 5)];
            var elsewhere = await site.AddClipAsync(Noon, manual, path: outside);
            var climbing = await site.AddClipAsync(
                Noon, manual, path: Path.Combine(site.Root, "..", Path.GetFileName(outside)));
            var missing = await site.AddClipAsync(Noon, manual, path: Path.Combine(site.Root, "absent.mp4"));

            foreach (var id in new[] { elsewhere, climbing, missing, Guid.NewGuid() })
                Assert.Equal(HttpStatusCode.NotFound, await StatusAsync(site, $"/clips/{id}/video"));
            Assert.Equal(HttpStatusCode.NotFound, await StatusAsync(site, $"/clips/{Guid.NewGuid()}"));
            Assert.Equal(HttpStatusCode.NotFound, await StatusAsync(site, "/clips/not-an-id/video"));
        }
        finally
        {
            File.Delete(outside);
        }
    }

    [Fact]
    public async Task The_health_check_asks_the_database()
    {
        await using var site = await Site.StartAsync(servers);

        Assert.Equal(HttpStatusCode.OK, await StatusAsync(site, "/healthz"));
    }

    [Fact]
    public async Task An_archived_clip_is_listed_only_when_asked_for()
    {
        await using var site = await Site.StartAsync(servers);
        var kept = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);
        var archived = await site.AddClipAsync(Noon.AddHours(1), [new Trigger("red_light", 5)]);

        using var page = await site.PostAsync($"/clips/{archived}?handler=Archive", ("on", "true"));
        Assert.Contains("Unarchive", await page.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        var index = await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken);
        var list = await site.Client.GetStringAsync("/types/red_light", TestContext.Current.CancellationToken);

        Assert.DoesNotContain(archived.ToString(), index);
        Assert.Contains("All 1", index);
        Assert.DoesNotContain(archived.ToString(), list);
        Assert.Contains(kept.ToString(), list);

        // The choice is kept in the browser, and holds on every page.
        using var included = await site.PostAsync("/?handler=Archived", ("include", "true"), ("returnUrl", "/types/red_light"));
        var all = await included.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.Equal("/types/red_light", included.RequestMessage?.RequestUri?.PathAndQuery);
        Assert.Contains(archived.ToString(), all);
        Assert.Contains("<span class=\"tag\">archived</span>", all);
        Assert.Contains("All 2", await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken));

        using var excluded = await site.PostAsync("/?handler=Archived", ("returnUrl", "https://elsewhere.example/"));
        Assert.Equal("/", excluded.RequestMessage?.RequestUri?.PathAndQuery);
        Assert.Contains("All 1", await excluded.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));

        await site.PostAsync($"/clips/{archived}?handler=Archive", ("on", "false"));
        Assert.Contains("All 2", await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task Opening_a_clip_marks_it_viewed()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);

        var before = await site.Client.GetStringAsync("/types/red_light", TestContext.Current.CancellationToken);
        await site.Client.GetStringAsync($"/clips/{id}", TestContext.Current.CancellationToken);
        var after = await site.Client.GetStringAsync("/types/red_light", TestContext.Current.CancellationToken);

        Assert.Contains("<li>", before);
        Assert.Contains("class=\"new\"", before);
        Assert.Contains("<li class=\"viewed\">", after);
        Assert.DoesNotContain("class=\"new\"", after);
    }

    [Fact]
    public async Task A_description_takes_the_place_of_the_detail_in_a_list()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5, Attrs: RedLightAttrs)]);

        using var described = await site.PostAsync(
            $"/clips/{id}?handler=Describe", ("description", "  A lorry, well after the light <changed>  "));
        var page = await described.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        var list = await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken);

        Assert.Contains("value=\"A lorry, well after the light &lt;changed&gt;\"", page);
        // The page still says what the event recorded.
        Assert.Contains("west-&gt;north, 1.2 s into red", page);
        Assert.Contains("A lorry, well after the light &lt;changed&gt;", list);
        Assert.DoesNotContain("1.2 s into red", list);

        await site.PostAsync($"/clips/{id}?handler=Describe", ("description", " "));
        Assert.Contains("1.2 s into red", await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken));
        Assert.Null(await site.ColumnAsync(id, "description"));
    }

    [Fact]
    public async Task A_false_positive_is_marked_and_vision_is_told_to_keep_it()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);

        using var marked = await site.PostAsync($"/clips/{id}?handler=FalsePositive", ("on", "true"));
        var page = await marked.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Contains("<span class=\"tag\">false positive</span>", page);
        Assert.Contains("Not a false positive", page);
        Assert.Contains("<span class=\"tag\">false positive</span>", await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken));
        // Retained: it is there for whoever subscribes later, as vision does when it starts.
        await using (var listener = await Listener.StartAsync(servers, VisionLink.ClipKeepTopic + id))
        {
            var keep = await listener.NextAsync();
            Assert.Equal("clip_keep/1", (string?)keep["schema"]);
            Assert.Equal(id.ToString(), (string?)keep["id"]);
            Assert.Equal(Site.Camera, (string?)keep["camera"]);
            Assert.True((bool?)keep["keep"]);
        }

        await site.PostAsync($"/clips/{id}?handler=FalsePositive", ("on", "false"));

        Assert.Null(await site.ColumnAsync(id, "false_positive_at"));
        await using var again = await Listener.StartAsync(servers, VisionLink.ClipKeepTopic + id);
        Assert.False((bool?)(await again.NextAsync())["keep"]);
    }

    [Fact]
    public async Task Without_the_broker_a_clip_is_not_marked_a_false_positive()
    {
        // Nothing listens on port 1.
        await using var site = await Site.StartAsync(servers, brokerPort: 1);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);

        using var refused = await site.PostAsync($"/clips/{id}?handler=FalsePositive", ("on", "true"));
        using var quick = await site.PostAsync("/?handler=QuickClip");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, refused.StatusCode);
        Assert.Null(await site.ColumnAsync(id, "false_positive_at"));
        Assert.Equal(HttpStatusCode.ServiceUnavailable, quick.StatusCode);
    }

    [Fact]
    public async Task A_quick_clip_asks_vision_for_what_it_has_just_seen()
    {
        await using var site = await Site.StartAsync(servers);
        await using var listener = await Listener.StartAsync(servers, VisionLink.ClipCommandTopic);

        using var asked = await site.PostAsync("/?handler=QuickClip");
        var waiting = await asked.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        var command = await listener.NextAsync();

        Assert.Equal("clip_command/1", (string?)command["schema"]);
        Assert.Equal(Site.Camera, (string?)command["camera"]);
        Assert.Equal(VisionLink.QuickClipReason, (string?)command["reason"]);
        Assert.Equal(90, (double?)command["pre_s"]);
        Assert.Equal(0, (double?)command["post_s"]);
        // It lands on the manual clips, which look again until the clip is there.
        var url = asked.RequestMessage!.RequestUri!.PathAndQuery;
        Assert.StartsWith("/types/manual?asked=", url);
        Assert.Contains("data-reload-after", waiting);

        var clip = await site.AddClipAsync(
            DateTimeOffset.UtcNow.AddSeconds(-Site.ClipSeconds), [new Trigger("manual", Site.ClipSeconds + 1, Reason: VisionLink.QuickClipReason)]);
        var arrived = await site.Client.GetStringAsync(url, TestContext.Current.CancellationToken);
        Assert.Contains(clip.ToString(), arrived);
        Assert.DoesNotContain("data-reload-after", arrived);
    }

    [Fact]
    public async Task A_quick_clip_that_never_comes_is_given_up_on()
    {
        await using var site = await Site.StartAsync(servers);
        var longAgo = Uri.EscapeDataString(DateTimeOffset.UtcNow.AddMinutes(-5).UtcDateTime.ToString("o"));

        var html = await site.Client.GetStringAsync($"/types/manual?asked={longAgo}", TestContext.Current.CancellationToken);

        Assert.Contains("has not arrived", html);
        Assert.DoesNotContain("data-reload-after", html);
    }

    [Fact]
    public async Task A_change_without_the_token_a_page_gives_is_refused()
    {
        await using var site = await Site.StartAsync(servers);
        var id = await site.AddClipAsync(Noon, [new Trigger("red_light", 5)]);

        using var response = await site.Client.PostAsync(
            $"/clips/{id}?handler=Archive",
            new FormUrlEncodedContent([KeyValuePair.Create("on", "true")]),
            TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Null(await site.ColumnAsync(id, "archived_at"));
    }

    static async Task<HttpStatusCode> StatusAsync(Site site, string path)
    {
        using var response = await site.Client.GetAsync(path, TestContext.Current.CancellationToken);
        return response.StatusCode;
    }

    /// <summary>The part of the index that is one type's column.</summary>
    static string Column(string html, string title)
    {
        var start = html.IndexOf($"<h2>{title}</h2>", StringComparison.Ordinal);
        Assert.True(start >= 0, $"no column titled {title}");
        return html[start..html.IndexOf("</section>", start, StringComparison.Ordinal)];
    }
}
