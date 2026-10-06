import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { entranceStyle } from "../core/motion/primitives";

/** Ligature bounds inside assets/agentcore-icon.svg. */
const PATH_A_AND_LOWER_C =
  "M 412,245 L 266,529 L 150,761 L 250,761 L 303,647 L 313,630 L 498,630 L 477,558 L 349,558 L 347,556 L 421,408 L 451,408 L 518,642 L 538,680 L 562,709 L 591,732 L 615,745 L 642,755 L 664,760 L 823,761 L 832,677 L 703,677 L 685,674 L 667,668 L 648,658 L 631,645 L 616,628 L 604,608 L 595,586 L 494,246 Z";
const PATH_UPPER_C =
  "M 877,245 L 729,245 L 699,249 L 667,258 L 636,272 L 610,289 L 586,311 L 564,338 L 547,369 L 582,486 L 598,486 L 602,450 L 607,423 L 617,401 L 640,369 L 654,356 L 671,345 L 695,334 L 714,329 L 732,327 L 868,327 Z";

export const SLOGAN = "协作，是更高级的智能";

export function LogoScene() {
  const frame = useCurrentFrame();

  const glyph = entranceStyle(frame, 4, 16);
  const word = entranceStyle(frame, 16, 16);
  const slogan = entranceStyle(frame, 30, 16);
  const glyphScale = interpolate(frame, [4, 24], [0.82, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const glowOpacity = interpolate(frame, [0, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const cardOpacity = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      className="bg-background"
      style={{
        alignItems: "center",
        justifyContent: "center",
        opacity: cardOpacity,
      }}
    >
      <div
        style={{
          position: "absolute",
          width: 900,
          height: 900,
          borderRadius: "50%",
          background:
            "radial-gradient(circle, var(--primary) 0%, transparent 62%)",
          opacity: 0.1 * glowOpacity,
          filter: "blur(20px)",
        }}
      />

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 28,
        }}
      >
        <div
          style={{
            opacity: glyph.opacity,
            transform: `${glyph.transform} scale(${glyphScale})`,
          }}
        >
          <svg
            width={169}
            height={120}
            viewBox="150 245 727 516"
            fill="currentColor"
            role="presentation"
            className="text-foreground"
          >
            <path d={PATH_A_AND_LOWER_C} />
            <path d={PATH_UPPER_C} />
          </svg>
        </div>

        <div
          className="text-foreground"
          style={{
            opacity: word.opacity,
            transform: word.transform,
            fontSize: 76,
            fontWeight: 600,
            letterSpacing: "-0.01em",
          }}
        >
          AgentCore
        </div>

        <div
          className="text-muted-foreground"
          style={{
            opacity: slogan.opacity,
            transform: slogan.transform,
            fontSize: 30,
            fontWeight: 500,
            letterSpacing: "0.04em",
          }}
        >
          {SLOGAN}
        </div>
      </div>
    </AbsoluteFill>
  );
}
