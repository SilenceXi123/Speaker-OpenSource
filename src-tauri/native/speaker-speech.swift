import AVFoundation
import CoreMedia
import Foundation
import Speech

struct SpeechFailure: Error, Encodable {
    let code: String
    let message: String
}

struct Capability: Encodable {
    let engine = "apple_speech"
    let locale = "zh_CN"
    let system_version: String
    let min_system_version = "26.0"
    let speech_available: Bool
    let model_installed: Bool
    let available: Bool
    let download_available: Bool
    let reason: String?
}

struct Word: Encodable, Equatable {
    let text: String
    let start_ms: Int
    let end_ms: Int
}

struct Segment: Encodable, Equatable {
    let text: String
    let start_ms: Int
    let end_ms: Int
    let words: [Word]
}

struct Transcript: Encodable {
    let engine = "apple_speech"
    let locale = "zh_CN"
    let duration_ms: Int
    let sample_rate_hz: Int
    let channel_count: UInt32
    let segments: [Segment]
    let system_version: String
}

@available(macOS 26.0, *)
@main struct SpeakerSpeech {
    static let locale = Locale(identifier: "zh_CN")
    static var systemVersion: String {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        return "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
    }

    static func capability() async -> Capability {
        let hardware = SpeechTranscriber.isAvailable
        let equivalent = await SpeechTranscriber.supportedLocale(equivalentTo: locale)
        let installedLocales = await SpeechTranscriber.installedLocales
        let supported = hardware && equivalent != nil
        let installed = supported && installedLocales.contains {
            $0.identifier.replacingOccurrences(of: "-", with: "_") == locale.identifier
        }
        return Capability(system_version: systemVersion, speech_available: hardware,
                          model_installed: installed, available: supported && installed,
                          download_available: supported && !installed,
                          reason: !hardware ? "当前设备不支持 Apple 本机识别" : !supported ? "当前系统不支持中文识别模型" : !installed ? "中文识别模型尚未安装，请联网准备模型" : nil)
    }

    static func module() -> SpeechTranscriber {
        SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [],
                          attributeOptions: [.audioTimeRange])
    }

    static func prepare() async throws -> Capability {
        let current = await capability()
        if current.available { return current }
        guard current.download_available else {
            throw SpeechFailure(code: "unsupported_device", message: current.reason ?? "当前设备不可用")
        }
        // This is the only action allowed to request system-managed model downloads.
        try await AssetInventory.reserve(locale: locale)
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [module()]) {
            try await request.downloadAndInstall()
        }
        let ready = await capability()
        guard ready.available else {
            throw SpeechFailure(code: "model_not_installed", message: "中文模型未安装完成，请检查网络后重试")
        }
        return ready
    }

    static func milliseconds(_ time: CMTime) throws -> Int {
        let seconds = CMTimeGetSeconds(time)
        guard seconds.isFinite, seconds >= 0, seconds < Double(Int.max) / 1000 else {
            throw SpeechFailure(code: "invalid_timing", message: "Apple 返回的音频时间范围无效")
        }
        return Int((seconds * 1000).rounded())
    }

    static func transcribe(path: String) async throws -> Transcript {
        let file: AVAudioFile
        do { file = try AVAudioFile(forReading: URL(fileURLWithPath: path)) }
        catch { throw SpeechFailure(code: "invalid_audio", message: "无法读取音频，请选择有效的音频文件") }
        let format = file.processingFormat
        guard file.length > 0, format.sampleRate.isFinite, format.sampleRate > 0, format.channelCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1024) else {
            throw SpeechFailure(code: "invalid_audio", message: "音频没有有效帧，无法转写")
        }
        do { try file.read(into: buffer) }
        catch { throw SpeechFailure(code: "invalid_audio", message: "音频帧无法解码") }
        guard buffer.frameLength > 0 else {
            throw SpeechFailure(code: "invalid_audio", message: "音频没有有效帧，无法转写")
        }
        file.framePosition = 0
        let duration = Double(file.length) / format.sampleRate
        guard duration.isFinite, duration * 1000 < Double(Int.max) else {
            throw SpeechFailure(code: "invalid_audio", message: "音频时长无效")
        }
        let status = await capability()
        guard status.available else {
            throw SpeechFailure(code: status.download_available ? "model_not_installed" : "unsupported_device",
                                message: status.reason ?? "本机识别不可用")
        }
        let transcriber = module()
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let results = Task { () throws -> [Segment] in
            var segments: [Segment] = []
            for try await result in transcriber.results where result.isFinal {
                let text = String(result.text.characters)
                if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
                var words: [Word] = []
                for run in result.text.runs {
                    let value = String(result.text[run.range].characters)
                    if value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
                    guard let range = run.audioTimeRange else {
                        throw SpeechFailure(code: "missing_timing", message: "Apple 未返回真实词时码，请保留原音频重试")
                    }
                    let start = try milliseconds(range.start)
                    let end = try milliseconds(CMTimeRangeGetEnd(range))
                    guard end >= start, Double(end) <= duration * 1000 + 1 else {
                        throw SpeechFailure(code: "invalid_timing", message: "Apple 返回的词时码超出音频范围")
                    }
                    words.append(Word(text: value, start_ms: start, end_ms: end))
                }
                guard let start = words.map(\.start_ms).min(), let end = words.map(\.end_ms).max() else {
                    throw SpeechFailure(code: "missing_timing", message: "Apple 未返回可用的词时码")
                }
                let segment = Segment(text: text, start_ms: start, end_ms: end, words: words)
                if segments.last != segment { segments.append(segment) }
            }
            return segments
        }
        do {
            if let end = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: end)
            } else {
                await analyzer.cancelAndFinishNow()
                results.cancel()
                throw SpeechFailure(code: "no_speech", message: "音频未产生可用转写结果")
            }
            let segments = try await results.value
            guard !segments.isEmpty else {
                throw SpeechFailure(code: "no_speech", message: "未识别到可用人声，请检查音频")
            }
            return Transcript(duration_ms: Int((duration * 1000).rounded()), sample_rate_hz: Int(format.sampleRate.rounded()),
                              channel_count: format.channelCount, segments: segments, system_version: systemVersion)
        } catch {
            await analyzer.cancelAndFinishNow()
            results.cancel()
            throw error
        }
    }

    static func emit<T: Encodable>(_ value: T) throws {
        try FileHandle(fileDescriptor: STDOUT_FILENO, closeOnDealloc: false).write(contentsOf: JSONEncoder().encode(value))
    }

    static func main() async {
        do {
            let args = CommandLine.arguments
            switch args.dropFirst().first {
            case "detect": try emit(await capability())
            case "prepare": try emit(try await prepare())
            case "transcribe" where args.count == 3: try emit(try await transcribe(path: args[2]))
            default: throw SpeechFailure(code: "invalid_request", message: "本机识别命令无效")
            }
        } catch {
            let failure = error as? SpeechFailure ?? SpeechFailure(code: "transcription_failed", message: "Apple 本机识别失败：\(error.localizedDescription)")
            try? emit(["error": failure])
            exit(1)
        }
    }
}
