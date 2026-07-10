// Shared helpers for the Scopio Lead Finder extension.

const Store = {
  get: (keys) => chrome.storage.local.get(keys),
  set: (obj) => chrome.storage.local.set(obj),
};

// Map Google Place "types" to Scopio's internal category taxonomy.
const _TYPE_MAP = [
  ["restaurant", "food"], ["cafe", "food"], ["bakery", "food"], ["bar", "food"], ["meal", "food"],
  ["hospital", "health"], ["doctor", "health"], ["pharmacy", "health"], ["dentist", "health"],
  ["clinic", "health"], ["physiotherapist", "health"],
  ["bank", "finance"], ["atm", "finance"], ["finance", "finance"], ["insurance", "finance"],
  ["lodging", "hospitality"], ["hotel", "hospitality"],
  ["store", "retail"], ["shop", "retail"], ["supermarket", "retail"], ["clothing", "retail"],
];
function mapCategory(types = []) {
  for (const t of types) {
    for (const [needle, cat] of _TYPE_MAP) {
      if (t.includes(needle)) return cat;
    }
  }
  return "services";
}

// Lightweight, transparent lead-quality score (0-100): online presence + reviews.
function leadScore(b) {
  let s = 10;
  if (b.website) s += 30;
  if (b.phone) s += 30;
  if (b.rating) s += Math.round(b.rating * 2);           // up to ~10
  if (b.reviews) s += Math.min(20, Math.round(b.reviews / 25)); // up to 20
  return Math.min(100, s);
}

// Phone normalization to a consistent string (keeps +, digits, spaces).
function cleanPhone(p) {
  return p ? p.replace(/[^\d+\s-]/g, "").trim() : null;
}
