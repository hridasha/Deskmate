export type HairStyle = "short" | "bowl" | "messy" | "long" | "bun" | "bald" | "bob" | "slick" | "curly";
export const HAIR_STYLES: HairStyle[] = ["short", "bowl", "messy", "long", "bun", "bald", "bob", "slick", "curly"];

/** Stylized costume for a character: signature clothes, hair and accessories (not a likeness of anyone). */
export type Look = {
  skin: string;
  pants: string;
  shirt: string;
  jacket?: string;
  tie?: string;
  hair: HairStyle;
  hairColor: string;
  glasses?: boolean;
  mustache?: boolean;
  /** Sleeves rolled up: forearms show skin. */
  rolled?: boolean;
  /** Torso width multiplier. */
  bulk?: number;
};

export const LOOKS: Record<string, Look> = {
  michael_scott: { skin: "#e9b48f", pants: "#2f3a52", shirt: "#f2f2f2", jacket: "#2f3a52", tie: "#b02a37", hair: "short", hairColor: "#7a5230" },
  dwight_schrute: { skin: "#e6b894", pants: "#4b3b2c", shirt: "#c9a227", tie: "#5a3b22", hair: "bowl", hairColor: "#6b4a2b", glasses: true },
  jim_halpert: { skin: "#e8b894", pants: "#3a3f4b", shirt: "#9ec3e8", tie: "#2f4d8f", hair: "messy", hairColor: "#6b4a2b", rolled: true },
  pam_beesly: { skin: "#f0c4a5", pants: "#3f3f52", shirt: "#f3eadf", jacket: "#e48fa8", hair: "long", hairColor: "#9a5b32" },
  oscar_martinez: { skin: "#c99572", pants: "#2d2d3a", shirt: "#8793d1", hair: "short", hairColor: "#1f1a17", glasses: true },
  angela_martin: { skin: "#f2cdb0", pants: "#3a3a46", shirt: "#f5f0e6", jacket: "#c3b1e1", hair: "bun", hairColor: "#e8c96a" },
  kevin_malone: { skin: "#e5b08a", pants: "#3a3a3a", shirt: "#8a8f99", tie: "#c9b79c", hair: "short", hairColor: "#4a3220", bulk: 1.3 },
  stanley_hudson: { skin: "#8a5a3c", pants: "#2d2f36", shirt: "#e9e9e9", tie: "#3d3d6b", hair: "bald", hairColor: "#a8a8a8", glasses: true, mustache: true },
  phyllis_vance: { skin: "#f0c4a5", pants: "#4a3f55", shirt: "#e9d9e9", jacket: "#c06c84", hair: "short", hairColor: "#b5b5b5" },
  andy_bernard: { skin: "#e8b894", pants: "#3b4a63", shirt: "#d9e6f2", jacket: "#7a1f4b", tie: "#f4a261", hair: "short", hairColor: "#b5762b" },
  ryan_howard: { skin: "#d2a07a", pants: "#202225", shirt: "#2a9d8f", hair: "short", hairColor: "#1f1a17", glasses: false },
  kelly_kapoor: { skin: "#c99572", pants: "#5b2333", shirt: "#e63946", hair: "long", hairColor: "#1f1a17" },
  creed_bratton: { skin: "#e6b894", pants: "#4f5348", shirt: "#8b8d7a", jacket: "#6b705c", tie: "#3d3d3d", hair: "messy", hairColor: "#d6d6d6", glasses: true },
  toby_flenderson: { skin: "#e5b08a", pants: "#3a3f4b", shirt: "#e8edf2", tie: "#6b7b8c", hair: "short", hairColor: "#7a5230", glasses: true },
  meredith_palmer: { skin: "#f0c4a5", pants: "#4a3a4f", shirt: "#d9a0a8", hair: "long", hairColor: "#c0643a" },
  erin_hannon: { skin: "#f2cdb0", pants: "#4a4a5a", shirt: "#f7e3c6", jacket: "#f4a261", hair: "long", hairColor: "#c9a063" },
  ada: { skin: "#c99572", pants: "#2b3a4a", shirt: "#bcd4e6", jacket: "#457b9d", hair: "bun", hairColor: "#1f1a17", glasses: true },
  marco: { skin: "#e5b08a", pants: "#6b5b3a", shirt: "#f4e3b0", jacket: "#e9c46a", hair: "messy", hairColor: "#4a3220" },
  nia: { skin: "#8a5a3c", pants: "#27402f", shirt: "#e6efe8", jacket: "#2d6a4f", hair: "bun", hairColor: "#1a1411", glasses: true },
  sam: { skin: "#f0c4a5", pants: "#3d3055", shirt: "#efe3fa", jacket: "#9d4edd", hair: "short", hairColor: "#7d7d7d", glasses: true },
  priya: { skin: "#c99572", pants: "#264d44", shirt: "#d8f5ec", hair: "long", hairColor: "#1a1411" },
  leo: { skin: "#e8b894", pants: "#2f2f38", shirt: "#f7c6d2", jacket: "#ef476f", hair: "short", hairColor: "#3a2a1a" },
  jan_levinson: { skin: "#f0c4a5", pants: "#222733", shirt: "#f1ede6", jacket: "#232a3b", hair: "bob", hairColor: "#c9a063" },
  gabe_lewis: { skin: "#e5b08a", pants: "#2b3445", shirt: "#e9eef5", tie: "#7a8aa5", hair: "slick", hairColor: "#3a2a1a" },
  holly_flax: { skin: "#f2cdb0", pants: "#4b4458", shirt: "#f7efe2", jacket: "#9fc4a8", hair: "curly", hairColor: "#7a3f2a" },
  roy_anderson: { skin: "#e8b894", pants: "#3a4150", shirt: "#7d8590", hair: "short", hairColor: "#3a2a1a" },
  karen_filippelli: { skin: "#f0c4a5", pants: "#2e2a38", shirt: "#e9d5e5", jacket: "#7b5ea7", hair: "bob", hairColor: "#6b3e26" },
  nellie_bertram: { skin: "#f2cdb0", pants: "#3a2f45", shirt: "#e8d8e8", jacket: "#a04a8a", hair: "curly", hairColor: "#c9a063" },
  robert_california: { skin: "#e5b08a", pants: "#1e1e24", shirt: "#f4f4f4", jacket: "#1e1e24", tie: "#7a1f3d", hair: "slick", hairColor: "#9a9a9a" },
  david_wallace: { skin: "#e8b894", pants: "#2c3548", shirt: "#e8eef7", jacket: "#2c3548", tie: "#335c9e", hair: "short", hairColor: "#6e6e6e", glasses: true },
  jo_bennett: { skin: "#f0c4a5", pants: "#4a4a58", shirt: "#f2ede4", jacket: "#b0413e", hair: "bob", hairColor: "#c4c4c4" },
  pete_miller: { skin: "#e5b08a", pants: "#34383f", shirt: "#c9d7e8", tie: "#5a6e8c", hair: "short", hairColor: "#4a3220" },
  darryl_philbin: { skin: "#8a5a3c", pants: "#2f3b52", shirt: "#5a6475", jacket: "#d8a31a", hair: "short", hairColor: "#1a1411" },
};

const SKINS = ["#f0c4a5", "#e6b894", "#c99572", "#8a5a3c", "#e9b48f"];
const HAIRS: HairStyle[] = ["short", "messy", "long", "bun", "bowl", "bob", "slick", "curly"];
const HAIR_COLORS = ["#1f1a17", "#4a3220", "#7a5230", "#b5762b", "#d9c07a", "#7d7d7d"];

function hash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** The scripted look for known characters; a color-themed outfit for anyone newly hired. */
export function lookFor(id: string, color: string, override?: Look): Look {
  if (override) return override;
  const known = LOOKS[id];
  if (known) return known;
  const h = hash(id);
  return {
    skin: SKINS[h % SKINS.length],
    pants: "#33363f",
    shirt: color,
    tie: h % 2 ? "#2d2d3a" : undefined,
    hair: HAIRS[h % HAIRS.length],
    hairColor: HAIR_COLORS[(h >> 3) % HAIR_COLORS.length],
    glasses: h % 3 === 0,
  };
}

/** Starting point for the character creator. */
export const DEFAULT_LOOK: Look = {
  skin: "#e6b894",
  pants: "#33363f",
  shirt: "#9ec3e8",
  hair: "short",
  hairColor: "#4a3220",
};
