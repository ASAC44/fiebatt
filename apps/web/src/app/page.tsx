"use client";

import dynamic from "next/dynamic";
import Image from "next/image";
import Link from "next/link";
import CardNav from "@/components/ui/card-nav";
import { Button } from "@/components/ui/button";
import { AnimatedSpan, Terminal, TypingAnimation } from "@/components/ui/terminal";
import GradualBlur from "@/components/GradualBlur";
import { SiteFooter } from "@/components/site-footer";

const TechnicalFlowExcalidraw = dynamic(
  () => import("@/components/technical-flow-excalidraw").then((mod) => mod.TechnicalFlowExcalidraw),
  {
    loading: () => (
      <div className="flex h-[620px] items-center justify-center rounded-[2rem] border border-black/10 bg-[#F4F4F1] text-sm text-neutral-500 md:h-[760px]">
        Loading flow chart...
      </div>
    ),
    ssr: false,
  },
);

const navItems = [
  {
    label: "About",
    bgColor: "#2D1B4E",
    textColor: "#fff",
    links: [
      { label: "Company", href: "#", ariaLabel: "About Company" },
      { label: "Careers", href: "#", ariaLabel: "About Careers" },
    ],
  },
  {
    label: "Projects",
    bgColor: "#6B5B8A",
    textColor: "#fff",
    links: [
      { label: "Featured", href: "#", ariaLabel: "Featured Projects" },
      { label: "Case Studies", href: "#", ariaLabel: "Project Case Studies" },
    ],
  },
  {
    label: "Contact",
    bgColor: "#ffffff",
    textColor: "#000",
    links: [
      { label: "Email", href: "#", ariaLabel: "Email us" },
      { label: "Twitter", href: "#", ariaLabel: "Twitter" },
      { label: "LinkedIn", href: "#", ariaLabel: "LinkedIn" },
    ],
  },
];

const featureCards = [
  {
    title: "Frame-aware edits",
    description: "Scrub to a moment, select the subject, and make the change exactly where it belongs.",
    video: "/feature-card-1.mp4",
  },
  {
    title: "Agent controlled",
    description: "Describe the outcome and let the backend run the generation, review, and timeline update.",
    video: "/feature-card-2.mp4",
  },
  {
    title: "Compare the result",
    description: "Review original and edited clips before committing the best version to your reel.",
    video: "/feature-card-3.mp4",
  },
];

const workflowSteps = [
  {
    step: "01",
    title: "Open a reel",
    copy: "Import footage or reopen a project. The timeline, source media, and accepted edits stay synced with the backend.",
    color: "bg-[#EEF3EA]",
  },
  {
    step: "02",
    title: "Describe the change",
    copy: "Scrub to the moment, select the region if needed, then ask for the edit in plain language through chat or CLI.",
    color: "bg-[#F6ECEF]",
  },
  {
    step: "03",
    title: "Review and commit",
    copy: "Compare variants against the original, accept the best result, propagate continuity, and export the final cut.",
    color: "bg-[#ECEFF5]",
  },
];

export default function Home() {
  return (
    <main className="bg-[#F4F4F1] text-black">
      <section
        className="relative min-h-screen overflow-hidden"
        style={{
          backgroundImage: "url('/hero-bg.png')",
          backgroundSize: "cover",
          backgroundPosition: "center",
          backgroundRepeat: "no-repeat",
        }}
      >
        <GradualBlur
          className="pointer-events-none"
          divCount={6}
          height="10rem"
          opacity={0.55}
          position="bottom"
          strength={1.35}
          zIndex={1}
        />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[2] h-40 bg-gradient-to-b from-transparent via-[#F4F4F1]/20 to-[#F4F4F1]/75" />
        <CardNav
          logo="/logo.png"
          logoAlt="Iris Logo"
          brandText="fiebatt"
          items={navItems}
          baseColor="#fff"
          menuColor="#000"
          buttonBgColor="#E11D48"
          buttonTextColor="#fff"
          buttonText="Projects"
        />
        <div className="flex min-h-screen items-start justify-center px-6 pt-48">
          <div className="relative z-10 px-8 py-7 text-center">
            <h1 className="mb-6 text-6xl font-light tracking-tight text-white drop-shadow-[0_2px_18px_rgba(0,0,0,0.45)] md:text-8xl">
              fiebatt.
            </h1>
            <p className="mx-auto max-w-2xl text-xl font-light text-white/90 drop-shadow-[0_2px_14px_rgba(0,0,0,0.45)] md:text-2xl">
              CLI-based surgical video editor for Claude and Codex
            </p>
            <Button asChild className="group mt-9 h-14 px-8 text-lg">
              <Link href="/projects">
                Projects
                <span className="ml-0 inline-block max-w-0 overflow-hidden opacity-0 transition-all duration-200 group-hover:ml-2 group-hover:max-w-5 group-hover:opacity-80">
                  →
                </span>
              </Link>
            </Button>
          </div>
        </div>
      </section>

      <section className="px-6 py-24 md:py-32">
        <div className="mx-auto max-w-7xl">
          <div className="mb-12 max-w-3xl">
            <h2 className="text-4xl font-semibold tracking-normal text-neutral-950 md:text-6xl">
              Built for exact edits.
            </h2>
            <p className="mt-5 text-lg leading-7 text-neutral-700 md:text-xl">
              Three parts of the workflow, shaped for fast review and repeatable agent-driven edits.
            </p>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            {featureCards.map((card) => (
              <article
                className="group overflow-hidden rounded-2xl border border-black/10 bg-white/55 p-3 transition-colors hover:bg-white/70"
                key={card.title}
              >
                <div className="overflow-hidden rounded-xl">
                  <video
                    autoPlay
                    className="aspect-square h-full w-full object-cover"
                    loop
                    muted
                    playsInline
                    src={card.video}
                  />
                </div>
                <div className="px-2 pb-3 pt-5">
                  <h3 className="text-2xl font-semibold tracking-normal text-neutral-950">
                    {card.title}
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-neutral-700">
                    {card.description}
                  </p>
                </div>
              </article>
            ))}
          </div>

          <div className="mt-24 grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
            <div>
              <p className="text-sm font-medium text-primary">CLI-01</p>
              <h2 className="mt-4 text-4xl font-semibold tracking-normal text-neutral-950 md:text-5xl">
                fiebatt works in the terminal.
              </h2>
              <p className="mt-5 text-lg leading-7 text-neutral-700">
                The fiebatt CLI wraps the backend for scripting, automation, and
                agent-driven editing. Any agent that can run shell commands can inspect
                projects, trigger localized changes, review variants, and export results
                without a custom integration.
              </p>
              <p className="mt-5 text-base leading-7 text-neutral-600">
                The same workflow, scriptable: preview footage, generate changes, score
                variants, accept the best result, and export when the cut is ready. A
                portable skill file gives Codex and Claude the setup context they need.
              </p>
            </div>

            <Terminal className="max-w-none border-black/10 bg-[#242421] text-neutral-100 shadow-none">
              <TypingAnimation className="text-neutral-100">
                $ fiebatt projects list
              </TypingAnimation>
              <AnimatedSpan className="text-neutral-400">
                found 3 active reels
              </AnimatedSpan>
              <TypingAnimation className="text-neutral-100">
                $ codex run &quot;make the subject jump at 00:03&quot;
              </TypingAnimation>
              <AnimatedSpan className="text-primary">
                analyzing timeline... selected source clip man_walking
              </AnimatedSpan>
              <AnimatedSpan className="text-primary">
                generating localized edit with qwen prompt plan
              </AnimatedSpan>
              <AnimatedSpan className="text-neutral-400">
                variant ready: compare original vs edited, then accept
              </AnimatedSpan>
              <TypingAnimation className="text-neutral-100">
                $ fiebatt export --project demo-reel
              </TypingAnimation>
              <AnimatedSpan className="text-primary">
                export complete ./exports/demo-reel.mp4
              </AnimatedSpan>
            </Terminal>
          </div>
        </div>
      </section>

      <section className="px-6 py-24 md:py-32">
        <div className="mx-auto max-w-7xl">
          <div className="mb-12 max-w-3xl">
            <p className="text-sm font-medium text-primary">The Platform</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-normal text-neutral-950 md:text-6xl">
              One surface for editing, chatting, comparing, and exporting.
            </h2>
          </div>

          <div className="overflow-hidden rounded-3xl border border-black/10 bg-white/60 p-2">
            <Image
              alt="fiebatt editor platform showing video preview, chat workflow, compare controls, and export tools"
              className="h-auto w-full rounded-[1.25rem] object-cover"
              height={1738}
              priority={false}
              src="/platform-hero.png"
              width={3024}
            />
          </div>
        </div>
      </section>

      <section className="border-t border-black/10 px-6 py-24 md:py-32">
        <div className="mx-auto max-w-7xl">
          <div className="mb-12 max-w-3xl">
            <p className="text-sm font-medium text-primary">How it works</p>
            <h2 className="mt-4 text-4xl font-semibold tracking-normal text-neutral-950 md:text-6xl">
              A tight loop from prompt to export.
            </h2>
          </div>

          <div className="grid gap-5 md:grid-cols-3">
            {workflowSteps.map((item) => (
              <article
                className={`${item.color} flex min-h-80 flex-col justify-between rounded-3xl border border-black/10 p-7 transition-colors hover:bg-white/70`}
                key={item.step}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-sm text-neutral-500">{item.step}</span>
                  <span className="size-2 rounded-full bg-primary/70" />
                </div>
                <div>
                  <h3 className="text-3xl font-semibold tracking-normal text-neutral-950">
                    {item.title}
                  </h3>
                  <p className="mt-5 text-base leading-7 text-neutral-700">
                    {item.copy}
                  </p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-black/10 px-6 py-24 md:py-32">
        <div className="mx-auto max-w-7xl">
          <div className="mb-14 grid gap-8 lg:grid-cols-[0.85fr_1.15fr] lg:items-end">
            <div>
              <p className="text-sm font-medium text-primary">Technical flow chart</p>
              <h2 className="mt-4 text-4xl font-semibold tracking-normal text-neutral-950 md:text-6xl">
                What happens after a prompt.
              </h2>
            </div>
            <p className="max-w-2xl text-lg leading-8 text-neutral-700 lg:justify-self-end">
              The same pipeline powers the editor UI and terminal workflow: collect intent,
              bind it to timeline context, plan tool calls, generate variants, then commit
              the accepted result back into the reel.
            </p>
            <Button asChild className="w-fit lg:col-start-2 lg:justify-self-end" variant="outline">
              <Link href="/fiebatt-technical-flow.excalidraw">
                Open Excalidraw
              </Link>
            </Button>
          </div>

          <TechnicalFlowExcalidraw />
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
