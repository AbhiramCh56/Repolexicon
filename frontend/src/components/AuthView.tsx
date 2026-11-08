import { ArrowLeftIcon, FolderGit2Icon } from "lucide-react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export default function AuthView({
  email,
  setEmail,
  password,
  setPassword,
  handleLogin,
  handleRegister,
  message,
  onBack,
}: any) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
      {/*
       * Static backdrop: one soft primary wash plus a faint grid, faded out by a
       * radial mask. Deliberately not animated — the app should feel calm.
       */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-b from-primary/10 via-transparent to-transparent"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(127,127,127,0.12)_1px,transparent_1px),linear-gradient(to_bottom,rgba(127,127,127,0.12)_1px,transparent_1px)] bg-[size:36px_36px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_72%)]"
      />

      <div className="relative z-10 w-full max-w-md">
        {onBack && (
          <button
            onClick={onBack}
            className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeftIcon className="size-4" />
            Back to home
          </button>
        )}

        <Card className="w-full">
          <CardHeader className="items-center text-center">
            <div className="mb-2 flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <FolderGit2Icon className="size-6" />
            </div>
            <CardTitle className="text-2xl">
              <span>Repo</span>
              <span className="text-primary">Lexicon</span>
            </CardTitle>
            <CardDescription>Sign in to your workspace</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {message && (
              <Alert>
                <AlertDescription>{message}</AlertDescription>
              </Alert>
            )}

            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="developer@example.com"
                className="h-9"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="h-9"
              />
            </div>

            <div className="flex gap-3 pt-1">
              <Button className="h-9 flex-1" onClick={handleLogin}>
                Login
              </Button>
              <Button
                variant="outline"
                className="h-9 flex-1"
                onClick={handleRegister}
              >
                Register
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
