// react-doctor.config.js
// Specifies project-level settings for react-doctor linting.

/** @type {import('react-doctor').Config} */
export default {
  // Entry points for dead-code analysis — tells react-doctor the import roots
  entry: ["src/main.tsx"],
  deadCode: {
    entry: ["src/main.tsx"],
    entryPoints: ["src/main.tsx"],
  },
};
