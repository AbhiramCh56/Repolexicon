import { ChevronUpIcon, Loader2Icon, LogOutIcon, PlusIcon } from "lucide-react"

import BrandMark from "./BrandMark"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"

/**
 * The shell for a workspace with no repositories yet.
 *
 * Deliberately mirrors the landing page (same backdrop, same mark, same dark
 * palette) because this *is* the product's first screen after sign-in: import a
 * repository, and the architecture takes over from here.
 */
export default function OnboardingView({
  repoUrl,
  setRepoUrl,
  onImport,
  importing,
  userProfile,
  onLogout,
}: {
  repoUrl: string
  setRepoUrl: (value: string) => void
  onImport: () => void
  importing: boolean
  userProfile: { email?: string } | null
  onLogout: () => void
}) {
  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#07070f] text-white">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(rgba(255,255,255,0.07)_1px,transparent_1px)] bg-[size:28px_28px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_78%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -top-48 left-1/2 h-[560px] w-[900px] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgba(99,102,241,0.28),transparent)] blur-2xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-64 bg-[radial-gradient(60%_100%_at_50%_100%,rgba(124,58,237,0.32),transparent)]"
      />

      <header className="relative z-20 flex items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2.5">
          <BrandMark className="size-7 text-indigo-400" />
          <span className="text-lg font-semibold tracking-tight">
            <span className="text-white">Repo</span>
            <span className="text-indigo-400">Lexicon</span>
          </span>
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="h-auto gap-2 px-2 py-2 text-white/80 hover:bg-white/10 hover:text-white"
            >
              <span className="flex size-7 flex-shrink-0 items-center justify-center rounded-full bg-indigo-500 text-xs font-semibold text-white">
                {(userProfile?.email?.[0] ?? "U").toUpperCase()}
              </span>
              <span className="hidden min-w-0 max-w-[16rem] truncate text-sm sm:inline">
                {userProfile?.email}
              </span>
              <ChevronUpIcon className="size-4 flex-shrink-0 text-white/50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="truncate">
              {userProfile?.email}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={onLogout}>
              <LogOutIcon />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <main className="relative z-10 flex flex-1 items-center justify-center px-6 pb-24">
        <div className="w-full max-w-2xl text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Import a repository
          </h1>
          <p className="mx-auto mt-4 max-w-lg text-base text-white/60">
            Paste a GitHub URL and RepoLexicon will chart its architecture while it
            indexes. Chat unlocks as soon as the index is ready.
          </p>

          <form
            className="mx-auto mt-8 flex w-full max-w-xl gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              onImport()
            }}
          >
            <Input
              value={repoUrl}
              onChange={(event) => setRepoUrl(event.target.value)}
              placeholder="https://github.com/owner/repo"
              aria-label="GitHub repository URL"
              disabled={importing}
              className="h-11 border-white/15 bg-white/[0.04] text-white placeholder:text-white/35 focus-visible:border-indigo-400/60"
            />
            <Button
              type="submit"
              disabled={importing || !repoUrl.trim()}
              className="h-11 flex-shrink-0 bg-indigo-500 px-5 text-white hover:bg-indigo-400"
            >
              {importing ? (
                <Loader2Icon className="size-4 animate-spin motion-reduce:animate-none" />
              ) : (
                <PlusIcon className="size-4" />
              )}
              Import
            </Button>
          </form>

          {/* Where the diagram lands once a repository exists. */}
          <div className="relative mt-10 flex h-52 items-center justify-center overflow-hidden rounded-2xl border border-dashed border-white/12 bg-white/[0.02]">
            <BrandMark className="absolute size-56 text-white/[0.045]" />
            <p className="relative text-sm text-white/40">
              Your architecture will appear here.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
