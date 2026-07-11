import Image from "next/image";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#F4F4F1] px-6 text-foreground">
      <section className="w-full max-w-xl text-center">
        <Link className="mx-auto mb-10 flex w-fit items-center gap-3 text-3xl font-semibold" href="/">
          <Image alt="" aria-hidden height={44} priority src="/logo.png" width={44} />
          fiebatt
        </Link>

        <p className="text-sm font-medium text-primary">404</p>
        <h1 className="mt-4 text-5xl font-semibold tracking-normal">
          This frame is missing.
        </h1>
        <p className="mx-auto mt-5 max-w-md text-base leading-7 text-muted-foreground">
          The page you opened is not in this edit. Head back and keep working from a valid timeline.
        </p>

        <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button asChild className="h-11 px-5">
            <Link href="/projects">Open projects</Link>
          </Button>
          <Button asChild className="h-11 px-5" variant="outline">
            <Link href="/">
              <ArrowLeft className="size-4" />
              Back home
            </Link>
          </Button>
        </div>
      </section>
    </main>
  );
}
