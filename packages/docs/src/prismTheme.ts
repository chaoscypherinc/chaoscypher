/**
 * Neon Noir Prism theme for Docusaurus code blocks.
 *
 * Colors match the --cc-accent-* token palette:
 *   Keywords → purple
 *   Strings → green
 *   Functions/methods → cyan
 *   Comments → muted
 *   Operators/punctuation → magenta
 *   Numbers → orange
 *   Class names → primary text
 */

import type { PrismTheme } from "prism-react-renderer";

const theme: PrismTheme = {
  plain: {
    color: "#c8c8e0",
    backgroundColor: "#0d0d1a",
  },
  styles: [
    {
      types: ["comment", "prolog", "doctype", "cdata"],
      style: { color: "#505068", fontStyle: "italic" as const },
    },
    {
      types: ["keyword", "builtin", "tag", "important"],
      style: { color: "#7b2ff7" },
    },
    {
      types: ["string", "attr-value", "template-string"],
      style: { color: "#39ff14" },
    },
    {
      types: ["function", "method", "attr-name"],
      style: { color: "#00fff0" },
    },
    {
      types: ["operator", "spread"],
      style: { color: "#ff2d95" },
    },
    {
      types: ["number", "boolean"],
      style: { color: "#ff6d00" },
    },
    {
      types: ["class-name", "maybe-class-name", "namespace"],
      style: { color: "#e0e0f0" },
    },
    {
      types: ["variable", "property"],
      style: { color: "#c8c8e0" },
    },
    {
      types: ["punctuation", "delimiter"],
      style: { color: "#808098" },
    },
    {
      types: ["selector", "regex"],
      style: { color: "#00fff0" },
    },
    {
      types: ["constant", "symbol"],
      style: { color: "#ff6d00" },
    },
    {
      types: ["decorator", "annotation"],
      style: { color: "#ff2d95" },
    },
    {
      types: ["inserted"],
      style: { color: "#39ff14" },
    },
    {
      types: ["deleted"],
      style: { color: "#ff2d95" },
    },
    {
      types: ["changed"],
      style: { color: "#ff6d00" },
    },
  ],
};

export default theme;
