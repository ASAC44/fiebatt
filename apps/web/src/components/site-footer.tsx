import Image from "next/image";
import Link from "next/link";

const footerLinks = [
  { label: "Projects", href: "/projects" },
  { label: "Editor", href: "/editor" },
  { label: "Docs", href: "#" },
  { label: "GitHub", href: "#" },
];

export function SiteFooter() {
  return (
    <footer className="border-t border-black/10 bg-[#F4F4F1] px-6 py-12">
      <div className="mx-auto grid max-w-7xl gap-10 md:grid-cols-[1.2fr_0.8fr] md:items-end">
        <div>
          <Link className="inline-flex items-center gap-3" href="/">
            <Image
              alt=""
              aria-hidden
              className="size-10"
              height={40}
              src="/logo.png"
              width={40}
            />
            <span className="text-2xl font-semibold tracking-normal text-neutral-950">
              fiebatt
            </span>
          </Link>
          <p className="mt-5 max-w-xl text-sm leading-6 text-neutral-600">
            A precise video editing surface for humans, terminals, and agents.
            Prompt changes, compare variants, and ship the final reel.
          </p>
        </div>

        <div className="flex flex-col gap-5 md:items-end">
          <nav className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-neutral-700">
            {footerLinks.map((link) => (
              <Link
                className="transition-colors hover:text-primary"
                href={link.href}
                key={link.label}
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-3 text-xs text-neutral-500">
            <span>© 2026 fiebatt</span>
            <span className="size-1 rounded-full bg-neutral-400" />
            <span>specific edits, authored outcomes</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
