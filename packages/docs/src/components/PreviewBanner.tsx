import React from "react";
import styles from "./PreviewBanner.module.css";

interface PreviewBannerProps {
  service?: string;
  expectedAt?: string;
}

export default function PreviewBanner({
  service = "This service",
  expectedAt,
}: PreviewBannerProps): JSX.Element {
  return (
    <aside className={styles.banner} role="note" aria-label="preview-notice">
      <strong>Preview — not yet generally available.</strong>{" "}
      {service} is documented now so the launch surface is final, but the hosted
      backend is not yet running. {expectedAt && `Target availability: ${expectedAt}.`}
      {" "}Self-host alternatives are described in the relevant page.
    </aside>
  );
}
