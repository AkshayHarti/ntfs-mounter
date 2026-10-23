# FUSE-T is a cask, and this Homebrew (7.0.10) no longer supports
# `depends_on cask: "..."` in a formula: `brew tap` rejects it with
# "Unsupported special dependency: :cask". A fatal requirement keeps the
# dependency visible and fails with the install command, the way macFUSE-based
# formulae in third-party taps do it.
class FuseTRequirement < Requirement
  fatal true

  satisfy(build_env: false) { File.exist?("/usr/local/lib/libfuse-t.dylib") }

  def message
    "FUSE-T is required. Install it with: brew install --cask fuse-t"
  end

  def display_s
    "FUSE-T"
  end
end

class Ntfs3gFuseT < Formula
  desc "NTFS-3G read/write NTFS driver linked against FUSE-T"
  homepage "https://github.com/macos-fuse-t/ntfs-3g"
  # Pinned to the tip of the fork's `edge` branch when this formula was written:
  #   gh api repos/macos-fuse-t/ntfs-3g/commits/edge --jq .sha
  #   -> f0e5cb0274e30334dd03aefb4fd5a14c239e6756
  # Tarball sha256 computed with:
  #   curl -sL <url> | shasum -a 256
  url "https://github.com/macos-fuse-t/ntfs-3g/archive/f0e5cb0274e30334dd03aefb4fd5a14c239e6756.tar.gz"
  # Upstream's own version (AC_INIT in configure.ac) is 2022.10.3; the fork has
  # no release tag, so the pinned commit is part of the version.
  version "2022.10.3-f0e5cb02"
  sha256 "911cec6d9015aa5d41579dfc4a606784179cb28e4341fb15d52173d8cc17fd2a"
  license all_of: ["GPL-2.0-or-later", "LGPL-2.0-or-later"]

  depends_on "autoconf" => :build
  depends_on "automake" => :build
  depends_on "libtool" => :build
  depends_on "pkgconf" => :build
  depends_on FuseTRequirement

  conflicts_with "ntfs-3g-mac", because: "both install an ntfs-3g linked against a different FUSE"

  def install
    # FUSE-T's dylib install name is @rpath/libfuse-t.dylib and lives in
    # /usr/local/lib, outside the Homebrew prefix, so the binary needs that
    # rpath baked in. The fork's src/Makefile.am already hardcodes
    # -I/usr/local/include/fuse and -lfuse-t, so configure needs no FUSE flags.
    #
    # Homebrew's superenv drops -I and -L paths outside the Homebrew prefix,
    # which would silently remove the hardcoded FUSE-T include dir and any
    # /usr/local/lib library path. CPATH and the -Wl,-L form are not filtered,
    # so the headers and libfuse-t.dylib are still found.
    ENV.append_path "CPATH", "/usr/local/include/fuse"
    ENV.append "LDFLAGS", "-Wl,-rpath,/usr/local/lib -Wl,-L/usr/local/lib"
    system "./autogen.sh"
    # ntfs-3g.probe is built in src/, under ENABLE_NTFS_3G, not in ntfsprogs/,
    # so --disable-ntfsprogs only skips the extra ntfsprogs utilities.
    # --exec-prefix=#{prefix} keeps rootlibdir equal to libdir; otherwise
    # configure sets rootlibdir=/lib and the libntfs-3g install hook tries to
    # move libntfs-3g.so* into /lib, which does not exist on macOS.
    system "./configure", "--prefix=#{prefix}", "--exec-prefix=#{prefix}", "--disable-ntfsprogs"
    system "make", "install"
  end

  def caveats
    <<~EOS
      This formula does not install its FUSE dependency; the cask must be
      installed separately before first use:

        brew install --cask fuse-t

      The cask records its library in /usr/local (outside the Homebrew
      prefix), so uninstalling this formula does not remove FUSE-T; remove the
      cask separately with brew uninstall --cask fuse-t (after unmounting).
    EOS
  end

  test do
    system bin/"ntfs-3g", "--version"
  end
end
