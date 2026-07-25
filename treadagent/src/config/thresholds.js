// Shop-configurable defaults for values that are NOT statutory (the
// statutory/provincial values live in the `regulations` NocoDB table,
// seeded by scripts/provision-nocodb.js — never hardcode those here).
// These are fallbacks used only when a shop hasn't overridden them in
// `shops` config.

export const DEFAULT_THRESHOLDS = {
  // Practical winter-performance replacement threshold, in 32nds.
  // ~3mm ≈ 4/32". Shop-configurable per CLAUDE.md / spec.
  practicalReplacement32nds: 4,
  // Irregular wear flag: spread between shoulder and centre readings,
  // in 32nds, beyond which the tech is prompted to inspect for
  // over/under-inflation, alignment, or cupping. This is a flag for
  // human inspection, never an automated diagnosis.
  irregularWearSpread32nds: 3,
  // Minimum number of dated readings before a wear-rate projection is
  // attempted. Below this, projectThreshold()/wearRate() return null
  // rather than guessing from a single data point.
  minReadingsForProjection: 2,
};

export default DEFAULT_THRESHOLDS;
