package com.faceverify.app.data

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase

@Entity(tableName = "person")
data class Person(
    @PrimaryKey val userId: String,
    val createdAt: Long = System.currentTimeMillis(),
)

@Entity(
    tableName = "embedding",
    foreignKeys = [ForeignKey(
        entity = Person::class,
        parentColumns = ["userId"],
        childColumns = ["ownerId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index("ownerId")],
)
data class Embedding(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val ownerId: String,
    val kind: String,                         // "anchor" (permanent) | "adaptive" (rolling)
    @ColumnInfo(typeAffinity = ColumnInfo.BLOB) val blob: ByteArray,   // encrypted embedding
    val createdAt: Long = System.currentTimeMillis(),
)

@Dao
interface FaceDao {
    @Insert suspend fun insertPerson(p: Person)
    @Insert suspend fun insertEmbedding(e: Embedding): Long

    @Query("SELECT * FROM person ORDER BY userId") suspend fun persons(): List<Person>
    @Query("SELECT userId FROM person ORDER BY userId") suspend fun userIds(): List<String>
    @Query("SELECT * FROM embedding") suspend fun allEmbeddings(): List<Embedding>
    @Query("SELECT * FROM embedding WHERE ownerId = :id ORDER BY id") suspend fun embeddingsFor(id: String): List<Embedding>
    @Query("SELECT id FROM embedding WHERE ownerId = :id AND kind = 'adaptive' ORDER BY id") suspend fun adaptiveIds(id: String): List<Long>
    @Query("SELECT id FROM embedding WHERE ownerId = :id AND kind = 'anchor' ORDER BY id") suspend fun anchorIds(id: String): List<Long>
    @Query("DELETE FROM embedding WHERE id = :rowId") suspend fun deleteEmbedding(rowId: Long)
    @Query("DELETE FROM person WHERE userId = :id") suspend fun deletePerson(id: String)
    @Query("SELECT COUNT(*) FROM person") suspend fun personCount(): Int
}

@Database(entities = [Person::class, Embedding::class], version = 1, exportSchema = false)
abstract class FaceDb : RoomDatabase() {
    abstract fun dao(): FaceDao

    companion object {
        @Volatile private var INSTANCE: FaceDb? = null
        fun get(context: Context): FaceDb = INSTANCE ?: synchronized(this) {
            INSTANCE ?: Room.databaseBuilder(
                context.applicationContext, FaceDb::class.java, "faceverify.db"
            ).build().also { INSTANCE = it }
        }
    }
}
