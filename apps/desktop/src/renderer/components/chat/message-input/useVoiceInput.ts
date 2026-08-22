import { notifyError } from "@/lib/toast";

function isNative(): boolean {
  return typeof window !== "undefined" && window.__NATIVE__ === true;
}
import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceInputState = "idle" | "recording" | "processing" | "error";

const MAX_DURATION_MS = 5 * 60 * 1000;

interface SpeechRecognitionResultList {
  length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionResult {
  isFinal: boolean;
  length: number;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
}

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message?: string;
}

interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  return (
    (window as Window & { webkitSpeechRecognition?: SpeechRecognitionCtor })
      .webkitSpeechRecognition ??
    (window as Window & { SpeechRecognition?: SpeechRecognitionCtor })
      .SpeechRecognition ??
    null
  );
}

function mapSpeechError(error: string): string | null {
  switch (error) {
    case "not-allowed":
      return "麦克风权限被拒绝，请在系统设置中允许访问";
    case "audio-capture":
      return "未检测到麦克风设备";
    case "network":
      return "网络错误，语音转写需要联网";
    case "no-speech":
      return "未检测到语音，请重试";
    case "aborted":
      return null;
    default:
      return "语音转写失败，请重试";
  }
}

export interface UseVoiceInputOptions {
  onTranscript: (text: string) => void;
}

export function useVoiceInput({ onTranscript }: UseVoiceInputOptions) {
  const SpeechRecognitionClass = getSpeechRecognition();
  const isSupported = SpeechRecognitionClass !== null || isNative();

  const [state, setState] = useState<VoiceInputState>("idle");
  const [interimText, setInterimText] = useState("");
  const [duration, setDuration] = useState(0);

  const stateRef = useRef<VoiceInputState>("idle");
  stateRef.current = state;

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const finalPartsRef = useRef<string[]>([]);
  const intentionalStopRef = useRef(false);
  const cancelledRef = useRef(false);
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startTimeRef = useRef(0);
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;

  const clearTimers = useCallback(() => {
    if (maxTimerRef.current) {
      clearTimeout(maxTimerRef.current);
      maxTimerRef.current = null;
    }
    if (durationTimerRef.current) {
      clearInterval(durationTimerRef.current);
      durationTimerRef.current = null;
    }
  }, []);

  const cleanupRecognition = useCallback(() => {
    clearTimers();
    const rec = recognitionRef.current;
    recognitionRef.current = null;
    if (rec) {
      rec.onresult = null;
      rec.onerror = null;
      rec.onend = null;
      try {
        rec.abort();
      } catch {
        /* already stopped */
      }
    }
  }, [clearTimers]);

  const finishWithTranscript = useCallback(() => {
    const text = finalPartsRef.current.join("");
    finalPartsRef.current = [];
    setInterimText("");
    setDuration(0);

    if (!text.trim()) {
      setState("idle");
      return;
    }

    setState("processing");
    onTranscriptRef.current(text);
    setState("idle");
  }, []);

  const stop = useCallback(() => {
    if (stateRef.current !== "recording") return;
    intentionalStopRef.current = true;
    clearTimers();
    const rec = recognitionRef.current;
    if (rec) {
      try {
        rec.stop();
      } catch {
        finishWithTranscript();
      }
    } else if (isNative()) {
      void import("@capgo/capacitor-speech-recognition").then(
        ({ SpeechRecognition }) => {
          void SpeechRecognition.stop().catch(() => {});
          finishWithTranscript();
        },
      );
    } else {
      finishWithTranscript();
    }
  }, [clearTimers, finishWithTranscript]);

  const cancel = useCallback(() => {
    if (stateRef.current !== "recording") return;
    cancelledRef.current = true;
    intentionalStopRef.current = true;
    finalPartsRef.current = [];
    setInterimText("");
    setDuration(0);
    clearTimers();
    cleanupRecognition();
    if (isNative()) {
      void import("@capgo/capacitor-speech-recognition").then(
        ({ SpeechRecognition }) => {
          void SpeechRecognition.stop().catch(() => {});
        },
      );
    }
    setState("idle");
  }, [clearTimers, cleanupRecognition]);

  const startNative = useCallback(async () => {
    const { SpeechRecognition } = await import(
      "@capgo/capacitor-speech-recognition"
    );
    const perm = await SpeechRecognition.requestPermissions();
    if (perm.speechRecognition !== "granted") {
      notifyError("麦克风权限被拒绝，请在系统设置中允许访问");
      setState("idle");
      return;
    }
    const { available } = await SpeechRecognition.available();
    if (!available) {
      notifyError("此设备不支持语音识别");
      setState("idle");
      return;
    }
    const language = navigator.language || "zh-CN";
    await SpeechRecognition.removeAllListeners();
    await SpeechRecognition.addListener("partialResults", (event) => {
      const text = event.matches?.[0] ?? "";
      setInterimText(text);
      finalPartsRef.current = text ? [text] : [];
    });
    await SpeechRecognition.addListener("listeningState", (event) => {
      if (event.status === "stopped" && !intentionalStopRef.current) {
        finishWithTranscript();
      }
    });
    await SpeechRecognition.start({ language, partialResults: true });
    setState("recording");
    durationTimerRef.current = setInterval(() => {
      setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
    }, 1000);
    maxTimerRef.current = setTimeout(() => {
      stop();
    }, MAX_DURATION_MS);
  }, [finishWithTranscript, stop]);

  const start = useCallback(() => {
    if (stateRef.current !== "idle") return;
    if (!SpeechRecognitionClass) {
      if (!isNative()) return;
      cancelledRef.current = false;
      intentionalStopRef.current = false;
      finalPartsRef.current = [];
      setInterimText("");
      setDuration(0);
      startTimeRef.current = Date.now();
      setState("recording");
      void startNative().catch(() => {
        clearTimers();
        notifyError("无法启动语音识别，请重试");
        setState("idle");
      });
      return;
    }

    cancelledRef.current = false;
    intentionalStopRef.current = false;
    finalPartsRef.current = [];
    setInterimText("");
    setDuration(0);
    startTimeRef.current = Date.now();

    const rec = new SpeechRecognitionClass();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language;

    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalPartsRef.current.push(transcript);
        } else {
          interim += transcript;
        }
      }
      setInterimText(interim);
    };

    rec.onerror = (event) => {
      if (cancelledRef.current) return;
      const message = mapSpeechError(event.error);
      cleanupRecognition();
      finalPartsRef.current = [];
      setInterimText("");
      setDuration(0);
      setState("idle");
      if (message) notifyError(message);
    };

    rec.onend = () => {
      if (cancelledRef.current) return;
      if (intentionalStopRef.current) {
        recognitionRef.current = null;
        finishWithTranscript();
        return;
      }
      // Unexpected end — try to restart if still in recording mode
      if (recognitionRef.current && !intentionalStopRef.current) {
        try {
          rec.start();
        } catch {
          recognitionRef.current = null;
          finishWithTranscript();
        }
      }
    };

    recognitionRef.current = rec;

    try {
      rec.start();
      setState("recording");

      durationTimerRef.current = setInterval(() => {
        setDuration(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 1000);

      maxTimerRef.current = setTimeout(() => {
        stop();
      }, MAX_DURATION_MS);
    } catch {
      cleanupRecognition();
      notifyError("无法启动语音识别，请重试");
      setState("idle");
    }
  }, [
    SpeechRecognitionClass,
    cleanupRecognition,
    clearTimers,
    finishWithTranscript,
    startNative,
    stop,
  ]);

  const toggle = useCallback(() => {
    if (stateRef.current === "idle") {
      start();
    } else if (stateRef.current === "recording") {
      stop();
    }
  }, [start, stop]);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      intentionalStopRef.current = true;
      cleanupRecognition();
    };
  }, [cleanupRecognition]);

  return {
    isSupported,
    state,
    interimText,
    duration,
    start,
    stop,
    cancel,
    toggle,
    isRecording: state === "recording",
    isProcessing: state === "processing",
  };
}
