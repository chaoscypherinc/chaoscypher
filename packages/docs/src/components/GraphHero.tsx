import { useEffect, useRef, useState } from 'react';
import Link from '@docusaurus/Link';
import CodeBlock from '@theme/CodeBlock';
import BrowserOnly from '@docusaurus/BrowserOnly';
import DataCrystalFallback from './hero/DataCrystalFallback';
import '../css/hero.css';

const RUN_CMD = `docker run -d -p 80:80 -v chaoscypher-data:/data \\\n  ghcr.io/chaoscypherinc/chaoscypher`;

export default function GraphHero(): JSX.Element {
  const cursorRef = useRef<HTMLSpanElement>(null);
  const [animating, setAnimating] = useState(false);
  useEffect(() => { cursorRef.current?.classList.add('hero-cursor--on'); }, []);

  return (
    <div className="hero-wrapper">
      <section className="hero-container">
        {/* base: static frame (SSR + no-JS + reduced motion); hidden once canvas animates */}
        <div className={'hero-crystal-fallback-wrap' + (animating ? ' is-hidden' : '')}>
          <DataCrystalFallback />
        </div>
        {/* overlay: animated canvas (client + motion only) */}
        <BrowserOnly>
          {() => {
            const DataCrystal = require('./hero/DataCrystal').default;
            return <DataCrystal onActive={() => setAnimating(true)} />;
          }}
        </BrowserOnly>
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
