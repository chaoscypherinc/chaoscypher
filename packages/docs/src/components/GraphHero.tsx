import { useEffect, useRef, useState } from 'react';
import Link from '@docusaurus/Link';
import CodeBlock from '@theme/CodeBlock';
import useBaseUrl from '@docusaurus/useBaseUrl';
import '../css/hero.css';

const RUN_CMD = `docker run -d -p 80:80 -v chaoscypher-data:/data \\\nghcr.io/chaoscypherinc/chaoscypher`;

export default function GraphHero(): JSX.Element {
  const cursorRef = useRef<HTMLSpanElement>(null);
  // SSR / no-JS / reduced-motion paint the video's poster (its first frame); the
  // ambient loop then plays on top — same image underneath, so there's no jump.
  const [showVideo, setShowVideo] = useState(false);
  const [videoReady, setVideoReady] = useState(false);
  const heroVideo = useBaseUrl('/video/hero-crystal.mp4');
  const heroPoster = useBaseUrl('/video/hero-crystal-poster.jpg');

  useEffect(() => {
    cursorRef.current?.classList.add('hero-cursor--on');
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!reduce) setShowVideo(true);
  }, []);

  return (
    <div className="hero-wrapper">
      <section className="hero-container">
        {/* base: the video's poster (its first frame) — same image the loop starts
            on, so SSR/no-JS/reduced-motion → video is a seamless hand-off, no jump */}
        <div
          className={'hero-crystal-fallback-wrap' + (videoReady ? ' is-hidden' : '')}
          style={{ backgroundImage: `url(${heroPoster})` }}
          aria-hidden="true"
        />
        {/* overlay: pre-rendered ambient loop (client + motion only) */}
        {showVideo && (
          <video
            className="hero-crystal-video"
            poster={heroPoster}
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
            aria-hidden="true"
            onCanPlay={() => setVideoReady(true)}
          >
            <source src={heroVideo} type="video/mp4" />
          </video>
        )}
        <div className="hero-vignette" />

        <div className="hero-content">
          <h1>Chaos Cypher</h1>
          <div className="hero-lede">
            <p className="hero-slogan">
              Decode knowledge from chaos<span className="hero-cursor" ref={cursorRef}>|</span>
            </p>
            <p className="hero-subdesc">
              The open-source knowledge graph engine — extract, search, and chat with
              your documents, locally.
            </p>
          </div>

          <Link className="hero-btn-primary" to="/docs/getting-started/quickstart">
            Get Started &#8594;
          </Link>

          <div className="hero-oneliner">
            <CodeBlock language="bash">{RUN_CMD}</CodeBlock>
          </div>

          <div className="hero-altrow">
            <span className="hero-alt-label">Use it via</span>
            {/* CLI + Python scroll to the on-page Get Started install cards */}
            <a className="hero-method" href="#install-cli">CLI</a>
            <span className="hero-sep">&middot;</span>
            <a className="hero-method" href="#install-python">Python library</a>
            <span className="hero-sep">&middot;</span>
            {/* MCP + REST API are access methods -> their docs */}
            <Link className="hero-method" to="/docs/user-guide/mcp">MCP</Link>
            <span className="hero-sep">&middot;</span>
            <Link className="hero-method" to="/docs/reference/api">REST API</Link>
            <span className="hero-divider">&bull;</span>
            <Link className="hero-devlink" to="/docs/developer-guide/quickstart">Developer Guide &#8594;</Link>
          </div>
        </div>
      </section>
      <div className="hero-fade" />
    </div>
  );
}
