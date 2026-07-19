// Default first-year topic sequences used in Phase 1 (generic mode), before a
// course outline is uploaded. Keys are matched loosely against course names.

export const DEFAULT_TOPIC_SEQUENCES = {
  CHEM: [
    'Measurement, units & significant figures',
    'Atomic structure & the periodic table',
    'Chemical formulas & nomenclature',
    'The mole & stoichiometry',
    'Chemical reactions in solution',
    'Gases & the gas laws',
    'Thermochemistry',
    'Electronic structure & periodicity',
    'Chemical bonding',
    'Molecular geometry (VSEPR)',
    'Solutions & concentration',
    'Introduction to equilibrium',
  ],
  MATH: [
    'Functions review & limits intuition',
    'Limits & limit laws',
    'Continuity',
    'The derivative as a limit',
    'Differentiation rules',
    'Derivatives of trig, exp & log functions',
    'Chain rule & implicit differentiation',
    'Related rates',
    'Linear approximation & differentials',
    'Curve sketching & extrema',
    'Optimization problems',
    'Introduction to integration',
  ],
  PHYS: [
    'Units, measurement & vectors',
    '1D kinematics',
    '2D kinematics & projectile motion',
    "Newton's laws of motion",
    'Applications of forces & friction',
    'Work & energy',
    'Conservation of energy',
    'Momentum & collisions',
    'Rotational motion',
    'Torque & static equilibrium',
    'Oscillations & waves',
    'Sound',
  ],
  BIOL: [
    'The chemistry of life',
    'Water, carbon & biological molecules',
    'Cell structure & function',
    'Membranes & transport',
    'Metabolism & enzymes',
    'Cellular respiration',
    'Photosynthesis',
    'Cell communication',
    'The cell cycle & mitosis',
    'Meiosis & sexual life cycles',
    'Mendelian genetics',
    'DNA structure & replication',
  ],
};

export const KNOWN_COURSES = {
  'BIOL 1110': { title: 'Introductory Biology I', key: 'BIOL' },
  'CHEM 1110': { title: 'Chemistry for the Sciences I', key: 'CHEM' },
  'PHYS 1101': { title: 'Physics for the Life Sciences I', key: 'PHYS' },
  'PHYS 1120': { title: 'Physics for Physical & Applied Sciences I', key: 'PHYS' },
  'MATH 1120': { title: 'Differential Calculus', key: 'MATH' },
};

export function topicSequenceFor(courseName) {
  const upper = (courseName || '').toUpperCase();
  for (const key of Object.keys(DEFAULT_TOPIC_SEQUENCES)) {
    if (upper.includes(key)) return DEFAULT_TOPIC_SEQUENCES[key];
  }
  // Unknown subject: give a neutral scaffold the student can rename later.
  return Array.from({ length: 12 }, (_, i) => `Week ${i + 1} topics`);
}

export function titleFor(courseName) {
  const upper = (courseName || '').toUpperCase().trim();
  return KNOWN_COURSES[upper]?.title || '';
}
