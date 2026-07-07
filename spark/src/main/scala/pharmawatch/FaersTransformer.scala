package pharmawatch

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.{DateType, DoubleType, IntegerType}

object FaersTransformer {

  val SourceTable = "glue_catalog.pharmawatch.faers_events"
  val TargetTable = "glue_catalog.pharmawatch_clean.faers_events"

  def transform(spark: SparkSession): DataFrame = {
    val raw = spark.read.format("iceberg").table(SourceTable)

    val cast = raw
      .withColumn("age", col("age").cast(IntegerType))
      .withColumn("weight", col("weight").cast(DoubleType))
      .withColumn("report_date", col("report_date").cast(DateType))

    val filtered = cast
      .filter(col("drug_name").isNotNull && length(trim(col("drug_name"))) > 0)

    val normalized = filtered
      .withColumn("drug_name", upper(trim(col("drug_name"))))

    val enriched = normalized
      .withColumn(
        "age_group",
        when(col("age").isNull, lit("unknown"))
          .when(col("age") < 18, lit("0-17"))
          .when(col("age") < 35, lit("18-34"))
          .when(col("age") < 65, lit("35-64"))
          .otherwise(lit("65+"))
      )
      .withColumn("is_serious", col("severity") === lit(1))

    val withOutcomes = enriched
      .withColumn("hospitalization", coalesce(col("hospitalization").cast(IntegerType), lit(0)))
      .withColumn("death", coalesce(col("death").cast(IntegerType), lit(0)))
      .withColumn("disability", coalesce(col("disability").cast(IntegerType), lit(0)))

    withOutcomes.dropDuplicates("report_id")
  }

  def run(spark: SparkSession): Unit = {
    val df = transform(spark)
    df.writeTo(TargetTable).using("iceberg").createOrReplace()
  }
}
