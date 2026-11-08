import {
  ChevronUpIcon,
  FolderGit2Icon,
  LogOutIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react"

import RepoStatusBadge from "./RepoStatusBadge"
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
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { embeddingPercent, type Repo } from "../lib/repos"

type SidebarProps = {
  userProfile: { email?: string } | null
  repositories: Repo[]
  selectedRepoId: string
  onSelectRepo: (id: string) => void
  repoUrl: string
  setRepoUrl: (value: string) => void
  handleAddRepo: () => void
  handleReprocessRepo: (id: string | number) => void
  handleRequestDelete: (repo: Repo) => void
  handleLogout: () => void
}

export default function Sidebar({
  userProfile,
  repositories,
  selectedRepoId,
  onSelectRepo,
  repoUrl,
  setRepoUrl,
  handleAddRepo,
  handleReprocessRepo,
  handleRequestDelete,
  handleLogout,
}: SidebarProps) {
  return (
    <aside className="flex w-80 flex-shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-3 p-5">
        <div className="flex size-9 flex-shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <FolderGit2Icon className="size-5" />
        </div>
        <div className="min-w-0">
          <h2 className="truncate font-heading text-base font-semibold leading-tight">
            RepoLexicon
          </h2>
          <p className="truncate text-xs text-muted-foreground">
            {userProfile?.email}
          </p>
        </div>
      </div>

      <Separator />

      <div className="space-y-3 px-4 py-4">
        <Label
          htmlFor="repo-url"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Import repository
        </Label>
        <Input
          id="repo-url"
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAddRepo()}
          placeholder="https://github.com/..."
          className="h-9"
        />
        <Button
          className="w-full"
          onClick={handleAddRepo}
          disabled={!repoUrl.trim()}
        >
          <PlusIcon />
          Add to workspace
        </Button>
      </div>

      <Separator />

      <div className="flex min-h-0 flex-1 flex-col">
        <h3 className="px-4 pb-3 pt-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Your repositories
        </h3>
        <ScrollArea className="min-h-0 flex-1">
          <div className="px-4 pb-4">
            {repositories.length === 0 ? (
              <p className="text-sm italic text-muted-foreground">
                No repositories imported yet.
              </p>
            ) : (
              <ul className="space-y-2">
                {repositories.map((repo) => {
                  const active = String(repo.id) === String(selectedRepoId)
                  const percent = embeddingPercent(repo)
                  return (
                    <li key={repo.id}>
                      {/*
                       * A div rather than a button: the card contains its own
                       * action buttons, and nesting buttons is invalid HTML.
                       */}
                      <div
                        role="button"
                        tabIndex={0}
                        aria-current={active ? "true" : undefined}
                        onClick={() => onSelectRepo(String(repo.id))}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") return
                          event.preventDefault()
                          onSelectRepo(String(repo.id))
                        }}
                        className={`cursor-pointer rounded-lg border p-3 text-card-foreground shadow-xs transition-colors ${
                          active
                            ? "border-primary/60 bg-primary/10 ring-1 ring-primary/30"
                            : "bg-card hover:border-primary/30 hover:bg-accent/40"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div
                              className="truncate text-sm font-medium"
                              title={repo.full_name}
                            >
                              {repo.name}
                            </div>
                            <div
                              className="mt-0.5 truncate text-xs text-muted-foreground"
                              title={repo.full_name}
                            >
                              {repo.full_name}
                            </div>
                          </div>

                          <div className="flex flex-shrink-0 items-center gap-0.5">
                            {(!repo.is_queryable || repo.status === "failed") && (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon-xs"
                                    aria-label="Re-import repository"
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      handleReprocessRepo(repo.id)
                                    }}
                                  >
                                    <RefreshCwIcon />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Re-import</TooltipContent>
                              </Tooltip>
                            )}
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  className="text-muted-foreground hover:text-destructive"
                                  aria-label={`Delete ${repo.name}`}
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    handleRequestDelete(repo)
                                  }}
                                >
                                  <Trash2Icon />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Delete</TooltipContent>
                            </Tooltip>
                          </div>
                        </div>

                        <div className="mt-2.5">
                          <RepoStatusBadge repo={repo} />
                        </div>

                        {percent !== null && (
                          <div
                            className="mt-2.5 h-1 overflow-hidden rounded-full bg-muted"
                            role="progressbar"
                            aria-valuenow={percent}
                            aria-valuemin={0}
                            aria-valuemax={100}
                            aria-label={`Embedding ${percent}% complete`}
                          >
                            <div
                              className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out motion-reduce:transition-none"
                              style={{ width: `${percent}%` }}
                            />
                          </div>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </ScrollArea>
      </div>

      <Separator />

      <div className="p-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="h-auto w-full justify-start gap-2 px-2 py-2"
            >
              <span className="flex size-7 flex-shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                {(userProfile?.email?.[0] ?? "U").toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate text-left text-sm">
                {userProfile?.email}
              </span>
              <ChevronUpIcon className="size-4 flex-shrink-0 text-muted-foreground" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel className="truncate">
              {userProfile?.email}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={handleLogout}>
              <LogOutIcon />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </aside>
  )
}
