#!/usr/bin/env Rscript

# ===============================
# PCA plot – ggplot2
# ===============================

.libPaths(c(Sys.getenv("R_LIBS_USER"), .libPaths()))

library(ggplot2)
library(readr)
library(dplyr)

# argumentos
args <- commandArgs(trailingOnly = TRUE)
eigenvec <- args[1]
eigenval <- args[2]
outpdf   <- args[3]

# ler eigenvalues (variância explicada)
eigval <- scan(eigenval)
pc1_var <- round(eigval[1] / sum(eigval) * 100, 1)
pc2_var <- round(eigval[2] / sum(eigval) * 100, 1)

# ler PCA (PLINK writes min(10, samples) PCs without a header; readr 2 dropped read_table2)
pca <- read.table(eigenvec, header = FALSE, stringsAsFactors = FALSE)
names(pca) <- c("FID", "IID", paste0("PC", seq_len(ncol(pca) - 2)))

# plot
p <- ggplot(pca, aes(x = PC1, y = PC2, label = IID)) +
  geom_point(size = 3) +
  geom_text(vjust = -0.8, size = 3.5) +
  theme_bw() +
  labs(
    title = "PCA of Leishmania braziliensis strains",
    x = paste0("PC1 (", pc1_var, "%)"),
    y = paste0("PC2 (", pc2_var, "%)")
  )

ggsave(outpdf, p, width = 7, height = 5)