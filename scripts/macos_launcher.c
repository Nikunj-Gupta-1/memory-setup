/*
 * Native macOS Universal Launcher for Memory Setup.app
 * Runs .venv Python in-process via dlopen(libpython) so macOS LaunchServices
 * registers the app as "Memory Setup" with its bundle icon and zero Terminal window.
 */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef int (*py_bytes_main_fn)(int argc, char **argv);

static void prepend_path_entries(void) {
    const char *home = getenv("HOME");
    const char *cur = getenv("PATH");
    char local_bin[PATH_MAX];
    local_bin[0] = '\0';
    if (home && home[0] != '\0') {
        snprintf(local_bin, sizeof(local_bin), "%s/.local/bin", home);
    }

    char new_path[8192];
    if (local_bin[0] != '\0') {
        snprintf(
            new_path,
            sizeof(new_path),
            "/opt/homebrew/bin:/usr/local/bin:%s:%s",
            local_bin,
            cur ? cur : "/usr/bin:/bin:/usr/sbin:/sbin"
        );
    } else {
        snprintf(
            new_path,
            sizeof(new_path),
            "/opt/homebrew/bin:/usr/local/bin:%s",
            cur ? cur : "/usr/bin:/bin:/usr/sbin:/sbin"
        );
    }
    setenv("PATH", new_path, 1);
}

static void redirect_logs_if_detached(void) {
    if (isatty(STDOUT_FILENO)) {
        return;
    }
    struct stat st;
    if (fstat(STDOUT_FILENO, &st) == 0 && (S_ISFIFO(st.st_mode) || S_ISREG(st.st_mode))) {
        return;
    }
    const char *home = getenv("HOME");
    if (!home || home[0] == '\0') {
        return;
    }
    char log_dir[PATH_MAX];
    snprintf(log_dir, sizeof(log_dir), "%s/Library/Logs", home);
    mkdir(log_dir, 0755);

    char log_path[PATH_MAX];
    snprintf(log_path, sizeof(log_path), "%s/MemorySetup.log", log_dir);
    int fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) {
        dup2(fd, STDOUT_FILENO);
        dup2(fd, STDERR_FILENO);
        if (fd > STDERR_FILENO) {
            close(fd);
        }
    }
}

static int read_pyvenv_home(const char *cfg_path, char *out_home, size_t out_size) {
    FILE *fp = fopen(cfg_path, "r");
    if (!fp) {
        return 0;
    }
    char line[PATH_MAX];
    int found = 0;
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "home", 4) == 0) {
            char *eq = strchr(line, '=');
            if (!eq) {
                continue;
            }
            char *val = eq + 1;
            while (*val == ' ' || *val == '\t') {
                val++;
            }
            size_t len = strlen(val);
            while (len > 0 && (val[len - 1] == '\n' || val[len - 1] == '\r' || val[len - 1] == ' ')) {
                val[--len] = '\0';
            }
            if (len > 0 && len < out_size) {
                snprintf(out_home, out_size, "%s", val);
                found = 1;
                break;
            }
        }
    }
    fclose(fp);
    return found;
}

static void *try_dlopen_pattern(const char *pattern, py_bytes_main_fn *out_main) {
    glob_t g;
    memset(&g, 0, sizeof(g));
    if (glob(pattern, 0, NULL, &g) != 0) {
        globfree(&g);
        return NULL;
    }
    void *handle = NULL;
    for (size_t i = 0; i < g.gl_pathc; i++) {
        const char *cand = g.gl_pathv[i];
        void *h = dlopen(cand, RTLD_NOW | RTLD_GLOBAL);
        if (!h) {
            continue;
        }
        py_bytes_main_fn fn = (py_bytes_main_fn)dlsym(h, "Py_BytesMain");
        if (fn) {
            handle = h;
            *out_main = fn;
            break;
        }
        dlclose(h);
    }
    globfree(&g);
    return handle;
}

static void *find_libpython(const char *repo_root, const char *venv_python, py_bytes_main_fn *out_main) {
    char cfg_path[PATH_MAX];
    snprintf(cfg_path, sizeof(cfg_path), "%s/.venv/pyvenv.cfg", repo_root);

    char py_home[PATH_MAX];
    char resolved_py[PATH_MAX];
    const char *bases[2] = {NULL, NULL};
    int n_bases = 0;

    if (read_pyvenv_home(cfg_path, py_home, sizeof(py_home))) {
        bases[n_bases++] = py_home;
    }
    if (realpath(venv_python, resolved_py) != NULL) {
        bases[n_bases++] = dirname(resolved_py);
    }

    for (int i = 0; i < n_bases; i++) {
        if (!bases[i]) {
            continue;
        }
        char pat[PATH_MAX];
        snprintf(pat, sizeof(pat), "%s/../lib/libpython3.[0-9]*.dylib", bases[i]);
        void *h = try_dlopen_pattern(pat, out_main);
        if (h) return h;

        snprintf(pat, sizeof(pat), "%s/../Python", bases[i]);
        h = try_dlopen_pattern(pat, out_main);
        if (h) return h;

        snprintf(pat, sizeof(pat), "%s/../../Python", bases[i]);
        h = try_dlopen_pattern(pat, out_main);
        if (h) return h;
    }
    return NULL;
}

int main(int argc, char **argv) {
    char raw_exe[PATH_MAX];
    uint32_t size = sizeof(raw_exe);
    if (_NSGetExecutablePath(raw_exe, &size) != 0) {
        return 1;
    }

    char resolved_exe[PATH_MAX];
    if (!realpath(raw_exe, resolved_exe)) {
        return 1;
    }

    /* Walk up 4 components: MemorySetup -> MacOS -> Contents -> Memory Setup.app -> repo_root */
    char repo_root[PATH_MAX];
    snprintf(repo_root, sizeof(repo_root), "%s", resolved_exe);
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(repo_root, '/');
        if (!slash) {
            return 1;
        }
        if (slash == repo_root) {
            repo_root[1] = '\0';
        } else {
            *slash = '\0';
        }
    }
    if (chdir(repo_root) != 0) {
        return 1;
    }

    prepend_path_entries();
    setenv("LLAMA_ARG_CACHE_RAM", "0", 1);

    char venv_python[PATH_MAX];
    snprintf(venv_python, sizeof(venv_python), "%s/.venv/bin/python", repo_root);

    /* If .venv has not been bootstrapped yet, open Start_Memory_Setup.command once in Terminal. */
    if (access(venv_python, X_OK) != 0) {
        char cmd_script[PATH_MAX];
        snprintf(cmd_script, sizeof(cmd_script), "%s/Start_Memory_Setup.command", repo_root);
        pid_t pid = fork();
        if (pid == 0) {
            char *open_argv[] = {"/usr/bin/open", "-a", "Terminal", cmd_script, NULL};
            execv("/usr/bin/open", open_argv);
            _exit(127);
        }
        return 0;
    }

    redirect_logs_if_detached();

    char **py_argv = calloc((size_t)argc + 4, sizeof(char *));
    if (!py_argv) {
        return 1;
    }
    int py_argc = 0;
    py_argv[py_argc++] = venv_python;
    py_argv[py_argc++] = "-m";
    py_argv[py_argc++] = "memory_setup.cli";
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0) {
            continue;
        }
        py_argv[py_argc++] = argv[i];
    }
    py_argv[py_argc] = NULL;

    py_bytes_main_fn py_bytes_main = NULL;
    void *lib_handle = find_libpython(repo_root, venv_python, &py_bytes_main);
    if (lib_handle && py_bytes_main) {
        int rc = py_bytes_main(py_argc, py_argv);
        free(py_argv);
        return rc;
    }

    /* Fallback if Python binary has no loadable libpython dylib */
    execv(venv_python, py_argv);
    free(py_argv);
    return 1;
}
