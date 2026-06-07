import type { KnipConfig } from 'knip';

const config: KnipConfig = {
  ignoreDependencies: [
    // Stryker's typescript-checker is currently commented out in
    // stryker.config.mjs (see the comment block there explaining why it
    // can't run before `make types` regenerates src/types/generated/).
    // The dep is kept so re-enabling it is a one-line uncomment rather
    // than a full reinstall cycle. Remove from this allowlist when it
    // becomes active again.
    '@stryker-mutator/typescript-checker',
    // License scanner invoked via scripts/license_check_interface.py
    // -> `npx license-checker-rseidelsohn`, not imported in TS code.
    'license-checker-rseidelsohn',
  ],
  ignore: [
    // Stryker config is dynamically imported by the stryker CLI;
    // knip would otherwise flag the type import + the inline `mutator`
    // export as unused.
    'stryker.config.mjs',
  ],
  // React component Props interfaces are intentionally exported as the
  // component's public contract but typically only referenced inside the
  // same file (e.g. `function Foo(p: FooProps)`). Without this, knip
  // flags every <Component>Props as dead code.
  ignoreExportsUsedInFile: {
    interface: true,
    type: true,
  },
};

export default config;
