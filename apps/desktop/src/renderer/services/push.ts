import { uiGet, uiRemove, uiSet } from "@/lib/uiStorage";
import { registerDevice, unregisterDevice } from "@/services/devices";
import { Capacitor } from "@capacitor/core";
import {
  type ActionPerformed,
  PushNotifications,
  type Token,
} from "@capacitor/push-notifications";

function isNative(): boolean {
  return typeof window !== "undefined" && window.__NATIVE__ === true;
}

const LAST_TOKEN_KEY = "push.lastToken";

let currentToken: string | null = null;
let lastToken: string | null = null;
let pushDesired = false;
let pushGeneration = 0;

function pushNativeEnabled(): boolean {
  return import.meta.env.VITE_PUSH_ENABLED === "true";
}

function platform(): "ios" | "android" | "web" {
  const p = Capacitor.getPlatform();
  if (p === "ios") return "ios";
  if (p === "android") return "android";
  return "web";
}

function readPersistedToken(): string | null {
  const raw = uiGet<string>(LAST_TOKEN_KEY);
  return typeof raw === "string" && raw ? raw : null;
}

function writePersistedToken(token: string | null): void {
  if (token) uiSet(LAST_TOKEN_KEY, token);
  else uiRemove(LAST_TOKEN_KEY);
}

function rememberToken(token: string): void {
  currentToken = token;
  lastToken = token;
  writePersistedToken(token);
}

function tokenForUnregister(): string | null {
  return currentToken ?? lastToken ?? readPersistedToken();
}

async function registerIfStillDesired(
  token: string,
  generation: number,
): Promise<void> {
  if (!pushDesired || generation !== pushGeneration) return;
  try {
    await registerDevice(token, platform());
  } catch {
    return;
  }
  if (!pushDesired || generation !== pushGeneration) {
    try {
      await unregisterDevice(token);
    } catch {
      /* stale token pruned later */
    }
  }
}

export async function initPush(
  onOpenConversation: (conversationId: string) => void,
): Promise<() => void> {
  if (!isNative() || !pushNativeEnabled()) return () => {};
  if (!lastToken) lastToken = readPersistedToken();

  try {
    const registration = await PushNotifications.addListener(
      "registration",
      (token: Token) => {
        rememberToken(token.value);
        if (!pushDesired) return;
        void registerIfStillDesired(token.value, pushGeneration);
      },
    );
    const registrationError = await PushNotifications.addListener(
      "registrationError",
      () => {
        currentToken = null;
      },
    );
    const action = await PushNotifications.addListener(
      "pushNotificationActionPerformed",
      (event: ActionPerformed) => {
        const conversationId = event.notification.data?.conversation_id;
        if (typeof conversationId === "string" && conversationId) {
          onOpenConversation(conversationId);
        }
      },
    );
    return () => {
      void registration.remove();
      void registrationError.remove();
      void action.remove();
    };
  } catch {
    return () => {};
  }
}

export async function enablePush(): Promise<void> {
  if (!isNative() || !pushNativeEnabled()) return;
  pushDesired = true;
  pushGeneration += 1;
  try {
    let perm = await PushNotifications.checkPermissions();
    if (perm.receive === "prompt" || perm.receive === "prompt-with-rationale") {
      perm = await PushNotifications.requestPermissions();
    }
    if (perm.receive !== "granted") return;
    await PushNotifications.register();
  } catch {
    /* degrade to no push */
  }
}

export async function disablePush(): Promise<void> {
  if (!isNative() || !pushNativeEnabled()) return;
  pushDesired = false;
  pushGeneration += 1;
  const token = tokenForUnregister();
  currentToken = null;
  if (!token) return;
  try {
    await unregisterDevice(token);
    lastToken = null;
    writePersistedToken(null);
  } catch {
    /* keep lastToken for a later DELETE */
  }
}
