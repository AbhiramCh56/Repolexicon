import {
  ArrowRightIcon,
  GitBranchIcon,
  NetworkIcon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react"

import LandingArchitecture from "./LandingArchitecture"
import BrandMark from "../BrandMark"

const FEATURES = [
  {
    icon: NetworkIcon,
    title: "Architecture at a glance",
    body: "Directories, entrypoints and their relationships laid out automatically from a deterministic manifest.",
  },
  {
    icon: SearchIcon,
    title: "Ask the codebase",
    body: "Grounded answers about how the code works, with the files and symbols they came from.",
  },
  {
    icon: SparklesIcon,
    title: "Watch it come together",
    body: "Import a repository and see its structure build up live as files are indexed.",
  },
]

const STEPS = [
  { n: "1", title: "Connect", body: "Paste a GitHub URL. No local setup, no build step." },
  { n: "2", title: "Index", body: "We parse the tree and build a manifest plus a searchable index." },
  { n: "3", title: "Explore", body: "Chart the architecture and query it in plain language." },
]

const DOC_LINKS = [
  { icon: GitBranchIcon, label: "Getting started", href: "#" },
  { icon: NetworkIcon, label: "Architecture guide", href: "#" },
  { icon: SearchIcon, label: "API reference", href: "#" },
]

export default function LandingView({
  onGetStarted,
}: {
  onGetStarted: () => void
}) {
  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[#07070f] text-white">
      {/* Ambient background: dotted grid, a top glow, and an aurora at the foot. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(rgba(255,255,255,0.07)_1px,transparent_1px)] bg-[size:28px_28px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_78%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -top-48 left-1/2 h-[560px] w-[900px] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgba(99,102,241,0.30),transparent)] blur-2xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-72 bg-[radial-gradient(60%_100%_at_50%_100%,rgba(124,58,237,0.38),transparent)]"
      />

      <header className="relative z-20 mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2.5">
          <BrandMark className="size-7 text-indigo-400" />
          <span className="text-lg font-semibold tracking-tight">
            <span className="text-white">Repo</span>
            <span className="text-indigo-400">Lexicon</span>
          </span>
        </div>

        <div className="flex items-center gap-10">
          <nav className="hidden items-center gap-8 text-sm text-white/70 md:flex">
            <a href="#features" className="transition-colors hover:text-white">
              Features
            </a>
            <a href="#how" className="transition-colors hover:text-white">
              How it works
            </a>
            <a href="#docs" className="transition-colors hover:text-white">
              Docs
            </a>
          </nav>
          <button
            onClick={onGetStarted}
            className="rounded-lg bg-indigo-500 px-5 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/25 transition-colors hover:bg-indigo-400"
          >
            Get Started
          </button>
        </div>
      </header>

      <main className="relative z-10">
        <section className="mx-auto max-w-4xl px-6 pt-8 text-center sm:pt-14">
          <h1 className="text-6xl font-bold tracking-tight sm:text-7xl md:text-8xl">
            <span className="text-white">Repo</span>
            <span className="text-indigo-400">Lexicon</span>
          </h1>
          <p className="mt-6 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            The lexicon of your codebase.
          </p>
          <p className="mx-auto mt-5 max-w-2xl text-base text-white/60 sm:text-lg">
            Import a repository and instantly visualize its architecture,
            dependencies, and how everything connects.
          </p>
        </section>

        <div className="mx-auto mt-8 flex max-w-6xl justify-center px-6 sm:mt-12">
          <LandingArchitecture />
        </div>

        <div className="mt-10 flex flex-col items-center gap-3 px-6 sm:mt-14">
          <button
            onClick={onGetStarted}
            className="group inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-8 py-4 text-base font-semibold text-white shadow-xl shadow-indigo-500/30 transition-colors hover:bg-indigo-400"
          >
            Explore a Repository
            <ArrowRightIcon className="size-5 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
          </button>
          <p className="text-sm text-white/50">
            Import a GitHub repo to get started
          </p>
        </div>

        <section
          id="features"
          className="mx-auto mt-28 max-w-6xl scroll-mt-20 px-6 sm:mt-36"
        >
          <h2 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl">
            Everything you need to understand a repo
          </h2>
          <p className="mx-auto mt-4 max-w-xl text-center text-white/60">
            From a single URL to a navigable map of the code.
          </p>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {FEATURES.map((feature) => (
              <div
                key={feature.title}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-6 transition-colors hover:border-indigo-400/40 hover:bg-white/[0.05]"
              >
                <div className="flex size-11 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-300">
                  <feature.icon className="size-5" />
                </div>
                <h3 className="mt-5 text-lg font-semibold">{feature.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-white/60">
                  {feature.body}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section
          id="how"
          className="mx-auto mt-28 max-w-5xl scroll-mt-20 px-6 sm:mt-36"
        >
          <h2 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl">
            How it works
          </h2>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {STEPS.map((step) => (
              <div
                key={step.n}
                className="rounded-2xl border border-white/10 bg-white/[0.03] p-6"
              >
                <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-500 text-sm font-semibold text-white">
                  {step.n}
                </div>
                <h3 className="mt-5 text-lg font-semibold">{step.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-white/60">
                  {step.body}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section
          id="docs"
          className="mx-auto mt-28 max-w-5xl scroll-mt-20 px-6 pb-24 sm:mt-36"
        >
          <h2 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl">
            Docs
          </h2>
          <div className="mt-10 grid gap-4 sm:grid-cols-3">
            {DOC_LINKS.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-5 py-4 text-sm text-white/80 transition-colors hover:border-indigo-400/40 hover:text-white"
              >
                <link.icon className="size-5 text-indigo-300" />
                {link.label}
                <ArrowRightIcon className="ml-auto size-4 text-white/40" />
              </a>
            ))}
          </div>
        </section>
      </main>

      <footer className="relative z-10 border-t border-white/10 py-8">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-6 text-sm text-white/50 sm:flex-row">
          <div className="flex items-center gap-2.5">
            <BrandMark className="size-5 text-indigo-400" />
            <span>RepoLexicon</span>
          </div>
          <p>The lexicon of your codebase.</p>
        </div>
      </footer>
    </div>
  )
}
