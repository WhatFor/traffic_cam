using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using MQTTnet;
using MQTTnet.Exceptions;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using TrafficCam.Contracts;

namespace TrafficCam.Web;

/// <summary>
/// Tells the vision service what a person asked for, over the broker. It connects for each message:
/// these are buttons pressed a few times a day.
/// </summary>
public sealed class VisionLink(WebOptions options, ILogger<VisionLink> logger)
{
    public const string ClipCommandTopic = "trafficcam/v1/cmd/clip";
    /// <summary>Followed by the clip's id.</summary>
    public const string ClipKeepTopic = "trafficcam/v1/cmd/keep/";
    public const string QuickClipReason = "quick clip";
    static readonly TimeSpan Patience = TimeSpan.FromSeconds(5);

    /// <summary>What a page answers when the broker did not take a message.</summary>
    public static ContentResult Unreachable() => new()
    {
        StatusCode = StatusCodes.Status503ServiceUnavailable,
        Content = "The camera service could not be told, so nothing was changed. Go back and try again.",
    };

    /// <summary>Asks for a clip of what has just happened, ending now. False if the broker did not take it.</summary>
    public Task<bool> QuickClipAsync(CancellationToken cancellation) =>
        PublishAsync(
            ClipCommandTopic,
            new ClipCommand
            {
                Schema = "clip_command/1",
                Id = Guid.NewGuid(),
                Ts = DateTimeOffset.UtcNow,
                Camera = options.Camera,
                Reason = QuickClipReason,
                PreS = options.QuickClipSeconds,
                PostS = 0,
            },
            retain: false,
            cancellation);

    /// <summary>
    /// Says whether a clip's files are to outlast the usual retention. Retained, so vision hears it
    /// even if it is down now. False if the broker did not take it.
    /// </summary>
    public Task<bool> KeepAsync(Guid clip, bool keep, CancellationToken cancellation) =>
        PublishAsync(
            ClipKeepTopic + clip,
            new ClipKeep
            {
                Schema = "clip_keep/1",
                Id = clip,
                Ts = DateTimeOffset.UtcNow,
                Camera = options.Camera,
                Keep = keep,
            },
            retain: true,
            cancellation);

    async Task<bool> PublishAsync<T>(string topic, T record, bool retain, CancellationToken cancellation)
    {
        var mqtt = options.Mqtt;
        var connect = new MqttClientOptionsBuilder()
            .WithTcpServer(mqtt.Host, mqtt.Port)
            .WithProtocolVersion(MqttProtocolVersion.V500)
            .WithCredentials(mqtt.Username, mqtt.Password)
            .Build();
        var message = new MqttApplicationMessageBuilder()
            .WithTopic(topic)
            .WithPayload(JsonSerializer.SerializeToUtf8Bytes(record))
            .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
            .WithRetainFlag(retain)
            .Build();
        using var patience = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        patience.CancelAfter(Patience);
        using var client = new MqttClientFactory().CreateMqttClient();
        try
        {
            await client.ConnectAsync(connect, patience.Token);
            // With this quality of service it returns once the broker has acknowledged the message.
            var result = await client.PublishAsync(message, patience.Token);
            await client.DisconnectAsync(cancellationToken: patience.Token);
            return result.IsSuccess;
        }
        catch (Exception exception) when (exception is MqttCommunicationException or OperationCanceledException)
        {
            logger.LogWarning("Nothing sent to {Topic}: {Reason}", topic, exception.Message);
            return false;
        }
    }
}
