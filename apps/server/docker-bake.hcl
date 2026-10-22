# Local / GHA bake. Production deploy uses remote-build-deploy.mjs
# (guest-base content id + ACR registry cache) instead of this file.
#
#   set -a && source build-pins.env && set +a
#   docker buildx bake -f docker-bake.hcl api

variable "PYTHON_IMAGE" { default = "" }
variable "PYPI_INDEX" { default = "https://pypi.tuna.tsinghua.edu.cn/simple/" }
variable "APT_MIRROR" { default = "mirrors.aliyun.com" }
variable "PLAYWRIGHT_VERSION" { default = "" }
variable "PLAYWRIGHT_DOWNLOAD_HOST" { default = "" }
variable "INSTALL_BROWSER" { default = "1" }
variable "GUEST_BASE" { default = "" }
variable "GIT_SHA" { default = "unknown" }
variable "BUILT_AT" { default = "unknown" }
variable "API_TAGS" { default = "agentcore/api:local" }
variable "GUEST_TAGS" { default = "agentcore/guest-base:local" }

target "guest" {
  context    = "."
  dockerfile = "Dockerfile.guest"
  args = {
    PYTHON_IMAGE             = PYTHON_IMAGE
    PYPI_INDEX               = PYPI_INDEX
    APT_MIRROR               = APT_MIRROR
    PLAYWRIGHT_VERSION       = PLAYWRIGHT_VERSION
    PLAYWRIGHT_DOWNLOAD_HOST = PLAYWRIGHT_DOWNLOAD_HOST
    INSTALL_BROWSER          = INSTALL_BROWSER
  }
  tags = [GUEST_TAGS]
}

target "api" {
  context    = "."
  dockerfile = "Dockerfile"
  contexts = {
    guest_image = GUEST_BASE != "" ? GUEST_BASE : "target:guest"
  }
  args = {
    PYTHON_IMAGE = PYTHON_IMAGE
    PYPI_INDEX   = PYPI_INDEX
    APT_MIRROR   = APT_MIRROR
    GIT_SHA      = GIT_SHA
    BUILT_AT     = BUILT_AT
  }
  tags = split(",", API_TAGS)
}
