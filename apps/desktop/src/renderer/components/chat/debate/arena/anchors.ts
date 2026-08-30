export function roundAnchorId(roundNo: number): string {
  return `debate-round-${roundNo}`;
}

export function closingAnchorId(): string {
  return "debate-closing";
}

export function finaleAnchorId(): string {
  return "debate-finale";
}

export function speakerAnchorId(roundNo: number, sideKey: string): string {
  return `debate-speech-r${roundNo}-${sideKey}`;
}
