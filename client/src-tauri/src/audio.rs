//! マイク取得 → 16kHz mono PCM16 → 100ms チャンクで [`crate::ws::WsHandle`] へ送信。
//!
//! 仕様 §3 の音声フォーマット:
//!   - LINEAR_PCM, 16-bit signed LE, 16000 Hz, mono, 100 ms chunk
//!
//! 実装方針:
//! - cpal `default_input_device` の `default_input_config` で実機ネイティブのサンプル
//!   レートとチャネル数を取得する (Mac built-in mic は通常 48 kHz, 1〜2 ch)。
//! - cpal のコールバックは `Send` を要求するが `cpal::Stream` 自体は `!Send`。
//!   stream の寿命管理を専用 OS thread に閉じ込め、`AudioRecorder` は `mpsc::Sender`
//!   で `Start` / `Stop` コマンドを送るだけにしてある (tokio runtime 上から呼ぶ用)。
//! - リサンプリングは rubato `FftFixedIn` (固定入力サイズ、出力サイズはレシオ依存)。
//! - 入力サンプル形式は f32 / i16 / u16 に対応。multi-ch は単純平均で mono に落とす。

use std::sync::mpsc::{self, Receiver, Sender};
use std::thread::{self, JoinHandle};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use rubato::{FftFixedIn, Resampler};
use tracing::{info, warn};

use crate::ws::WsHandle;

const TARGET_SAMPLE_RATE: usize = 16000;
const CHUNK_MS: usize = 100;
/// 100 ms @ 16 kHz = 1600 samples.
const SAMPLES_PER_CHUNK: usize = TARGET_SAMPLE_RATE * CHUNK_MS / 1000;
/// PCM16 LE → 1 sample = 2 bytes。1 チャンク 3200 bytes。
const BYTES_PER_CHUNK: usize = SAMPLES_PER_CHUNK * 2;
/// リサンプラーへ渡す入力サイズ (入力レート基準)。host 側 callback サイズに合わせる必要は
/// なく、内部リングバッファで吸収する。
const RESAMPLER_CHUNK_IN: usize = 1024;
/// rubato 内部分割数。リアルタイム入力で 2〜4 程度が無難。
const RESAMPLER_SUB_CHUNKS: usize = 2;

enum AudioCommand {
    Start,
    Stop,
}

/// マイク録音タスクのハンドル。`start` / `stop` で push-to-talk を制御する。
pub struct AudioRecorder {
    cmd_tx: Sender<AudioCommand>,
    _thread: JoinHandle<()>,
}

impl AudioRecorder {
    pub fn new(ws: WsHandle) -> Self {
        let (tx, rx) = mpsc::channel();
        let thread = thread::Builder::new()
            .name("koecast-audio".to_string())
            .spawn(move || run_audio_thread(rx, ws))
            .expect("spawn koecast-audio thread");
        Self { cmd_tx: tx, _thread: thread }
    }

    pub fn start(&self) {
        if let Err(e) = self.cmd_tx.send(AudioCommand::Start) {
            warn!(?e, "audio start send failed (audio thread gone?)");
        }
    }

    pub fn stop(&self) {
        if let Err(e) = self.cmd_tx.send(AudioCommand::Stop) {
            warn!(?e, "audio stop send failed (audio thread gone?)");
        }
    }
}

fn run_audio_thread(rx: Receiver<AudioCommand>, ws: WsHandle) {
    let host = cpal::default_host();
    let device = match host.default_input_device() {
        Some(d) => d,
        None => {
            warn!("no default input device; audio recording disabled");
            return;
        }
    };
    let device_name = device
        .description()
        .map(|d| d.name().to_string())
        .unwrap_or_else(|_| "<unknown>".to_string());

    let config = match device.default_input_config() {
        Ok(c) => c,
        Err(e) => {
            warn!(?e, "no default input config; audio recording disabled");
            return;
        }
    };
    // cpal 0.17 から sample_rate() は u32 を直接返す (旧版は SampleRate(u32))
    let input_rate = config.sample_rate() as usize;
    let channels = config.channels() as usize;
    let sample_format = config.sample_format();
    info!(
        device_name,
        input_rate,
        channels,
        ?sample_format,
        target_rate = TARGET_SAMPLE_RATE,
        "audio device opened"
    );

    let stream_config: cpal::StreamConfig = config.into();
    let mut current_stream: Option<cpal::Stream> = None;

    while let Ok(cmd) = rx.recv() {
        match cmd {
            AudioCommand::Start => {
                if current_stream.is_some() {
                    warn!("audio start called while already running; ignoring");
                    continue;
                }
                match build_stream(
                    &device,
                    &stream_config,
                    sample_format,
                    input_rate,
                    channels,
                    ws.clone(),
                ) {
                    Ok(stream) => match stream.play() {
                        Ok(()) => {
                            info!("audio recording started");
                            current_stream = Some(stream);
                        }
                        Err(e) => warn!(?e, "stream.play failed"),
                    },
                    Err(e) => warn!(?e, "build_input_stream failed"),
                }
            }
            AudioCommand::Stop => {
                if let Some(s) = current_stream.take() {
                    // drop で Stream が止まる。明示 pause() してから drop の方が
                    // CoreAudio の挙動的に綺麗。
                    let _ = s.pause();
                    drop(s);
                    info!("audio recording stopped");
                }
            }
        }
    }
}

fn build_stream(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    sample_format: cpal::SampleFormat,
    input_rate: usize,
    channels: usize,
    ws: WsHandle,
) -> Result<cpal::Stream, cpal::BuildStreamError> {
    let resampler = FftFixedIn::<f32>::new(
        input_rate,
        TARGET_SAMPLE_RATE,
        RESAMPLER_CHUNK_IN,
        RESAMPLER_SUB_CHUNKS,
        1,
    )
    .expect("FftFixedIn::new with valid params");

    let processor = std::sync::Mutex::new(AudioProcessor::new(channels, resampler, ws));

    let err_fn = |err| warn!(?err, "cpal input stream error");

    match sample_format {
        cpal::SampleFormat::F32 => device.build_input_stream(
            config,
            move |data: &[f32], _info| {
                if let Ok(mut p) = processor.lock() {
                    p.feed_f32(data);
                }
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::I16 => device.build_input_stream(
            config,
            move |data: &[i16], _info| {
                if let Ok(mut p) = processor.lock() {
                    p.feed_i16(data);
                }
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::U16 => device.build_input_stream(
            config,
            move |data: &[u16], _info| {
                if let Ok(mut p) = processor.lock() {
                    p.feed_u16(data);
                }
            },
            err_fn,
            None,
        ),
        other => {
            warn!(?other, "unsupported sample format; assuming F32");
            device.build_input_stream(
                config,
                move |data: &[f32], _info| {
                    if let Ok(mut p) = processor.lock() {
                        p.feed_f32(data);
                    }
                },
                err_fn,
                None,
            )
        }
    }
}

struct AudioProcessor {
    channels: usize,
    resampler: FftFixedIn<f32>,
    ws: WsHandle,
    /// 入力モノラル化したサンプルを溜めるリングバッファ。RESAMPLER_CHUNK_IN 単位で
    /// 取り出して resampler に渡す。
    mono_buf: Vec<f32>,
    /// resampler 出力 (16kHz mono f32) を溜めるリングバッファ。SAMPLES_PER_CHUNK
    /// 単位で取り出して PCM16 化して送る。
    out_buf: Vec<f32>,
}

impl AudioProcessor {
    fn new(channels: usize, resampler: FftFixedIn<f32>, ws: WsHandle) -> Self {
        Self {
            channels,
            resampler,
            ws,
            mono_buf: Vec::with_capacity(RESAMPLER_CHUNK_IN * 4),
            out_buf: Vec::with_capacity(SAMPLES_PER_CHUNK * 4),
        }
    }

    fn feed_f32(&mut self, data: &[f32]) {
        self.downmix_into_mono(data, |s| s);
        self.drain_resampler();
    }

    fn feed_i16(&mut self, data: &[i16]) {
        let scale = 1.0 / i16::MAX as f32;
        self.downmix_into_mono(data, |s| (s as f32) * scale);
        self.drain_resampler();
    }

    fn feed_u16(&mut self, data: &[u16]) {
        let scale = 1.0 / i16::MAX as f32;
        self.downmix_into_mono(data, |s| ((s as i32 - 32768) as f32) * scale);
        self.drain_resampler();
    }

    fn downmix_into_mono<T: Copy, F: Fn(T) -> f32>(&mut self, data: &[T], to_f32: F) {
        if self.channels == 1 {
            for &s in data {
                self.mono_buf.push(to_f32(s));
            }
        } else {
            let ch = self.channels;
            let inv = 1.0 / ch as f32;
            for frame in data.chunks_exact(ch) {
                let mut sum = 0.0;
                for &s in frame {
                    sum += to_f32(s);
                }
                self.mono_buf.push(sum * inv);
            }
        }
    }

    fn drain_resampler(&mut self) {
        while self.mono_buf.len() >= RESAMPLER_CHUNK_IN {
            let chunk: Vec<f32> = self.mono_buf.drain(..RESAMPLER_CHUNK_IN).collect();
            let input = vec![chunk];
            match self.resampler.process(&input, None) {
                Ok(mut out) => {
                    if let Some(o) = out.pop() {
                        self.out_buf.extend(o);
                    }
                }
                Err(e) => warn!(?e, "resampler.process failed"),
            }
        }
        self.flush_chunks();
    }

    fn flush_chunks(&mut self) {
        while self.out_buf.len() >= SAMPLES_PER_CHUNK {
            let mut bytes = Vec::with_capacity(BYTES_PER_CHUNK);
            for s in self.out_buf.drain(..SAMPLES_PER_CHUNK) {
                let clamped = s.clamp(-1.0, 1.0);
                let i = (clamped * i16::MAX as f32) as i16;
                bytes.extend_from_slice(&i.to_le_bytes());
            }
            debug_assert_eq!(bytes.len(), BYTES_PER_CHUNK);
            self.ws.send_audio(bytes);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn samples_per_chunk_matches_spec() {
        // 仕様 §3: 16000 Hz × 100 ms = 1600 samples = 3200 bytes
        assert_eq!(SAMPLES_PER_CHUNK, 1600);
        assert_eq!(BYTES_PER_CHUNK, 3200);
    }
}
