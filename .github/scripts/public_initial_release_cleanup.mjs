import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const TEMPORARY_BASELINE_TAG = "v0.0.0";
const INITIAL_RELEASE_VERSION = "0.1.0";

async function git(cwd, args) {
  try {
    return await execFileAsync("git", args, { cwd, encoding: "utf8" });
  } catch (error) {
    const detail = String(error.stderr || error.stdout || error.message).trim();
    throw new Error(`git ${args.join(" ")} failed: ${detail}`);
  }
}

export async function prepare(_pluginConfig, context) {
  const { cwd, lastRelease, logger, nextRelease } = context;
  if (lastRelease?.gitTag !== TEMPORARY_BASELINE_TAG) {
    return;
  }
  if (lastRelease.version !== "0.0.0" || nextRelease?.version !== INITIAL_RELEASE_VERSION) {
    throw new Error("temporary v0.0.0 is valid only while preparing the initial v0.1.0 release");
  }

  const [{ stdout: tagHead }, { stdout: remoteTag }] = await Promise.all([
    git(cwd, ["rev-parse", `${TEMPORARY_BASELINE_TAG}^{commit}`]),
    git(cwd, ["ls-remote", "--tags", "origin", `refs/tags/${TEMPORARY_BASELINE_TAG}`]),
  ]);
  if (tagHead.trim() !== lastRelease.gitHead) {
    throw new Error("temporary v0.0.0 does not match semantic-release lastRelease.gitHead");
  }
  if (remoteTag.trim()) {
    throw new Error("remote v0.0.0 is forbidden");
  }

  await git(cwd, ["tag", "--delete", TEMPORARY_BASELINE_TAG]);
  logger?.log("Removed runner-local v0.0.0 before publishing v0.1.0");
}
